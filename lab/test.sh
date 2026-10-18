#!/usr/bin/env bash
# 설계 요구사항을 랩에서 하나씩 확인한다. 각 줄이 PASS면 해당 요구사항이 동작으로 증명된 것
# 사용법: ./test.sh            (기본 점검 + 장애 시험)
#         ./test.sh quick      (기본 점검만)
set -u
N=clab-elice
PASS=0; FAIL=0
x() { docker exec "$N-$1" sh -c "$2" >/dev/null 2>&1; }
ok() { if x "$2" "$3"; then echo "PASS  $1"; PASS=$((PASS+1)); else echo "FAIL  $1"; FAIL=$((FAIL+1)); fi; }
ng() { if x "$2" "$3"; then echo "FAIL  $1"; FAIL=$((FAIL+1)); else echo "PASS  $1"; PASS=$((PASS+1)); fi; }

echo "== 1. 언더레이 (BGP unnumbered)"
for l in leaf1a leaf1b leaf2a leaf2b leaf3a leaf3b; do
  ok "$l 스파인 세션 2개" $l "[ \$(vtysh -c 'show bgp summary json' | grep -c '\"state\":\"Established\"') -ge 2 ]"
done
for s in spine1 spine2; do
  ok "$s 리프 세션 6개" $s "[ \$(vtysh -c 'show bgp summary json' | grep -c '\"state\":\"Established\"') -ge 6 ]"
done

echo "== 2. 오버레이 (EVPN-VXLAN, 랙을 넘는 같은 서브넷)"
ok "ctrl1(R1) → ctrl3(R3) K8S-NODE"            ctrl1 "ping -c2 -W1 10.10.16.13"
ok "wrk1(R1) → wrk5(R3) K8S-NODE"              wrk1  "ping -c2 -W1 10.10.16.25"
ok "ceph4(R3) → ceph1(R1) Ceph public 점보 9000" ceph4 "ping -c2 -W1 -M do -s 8972 10.10.20.31"
ok "ceph4(R3) → ceph1(R1) Ceph cluster 점보 9000" ceph4 "ping -c2 -W1 -M do -s 8972 10.10.24.31"
ok "wrk5 → ceph1 스토리지 다리로 직접 (방화벽 미경유)" wrk5 "ping -c2 -W1 10.10.20.31 && ip route get 10.10.20.31 | grep -q bond1.120"
ng "K8S-NODE는 1500 (점보 불가)"               ctrl1 "ping -c1 -W1 -M do -s 1600 10.10.16.13"

echo "== 3. 분리 (VRF, 방화벽)"
ng "ctrl1 → Ceph cluster(REPL VRF) 도달 불가"  ctrl1 "ping -c1 -W1 10.10.24.31"
ng "wrk1 → Ceph cluster(REPL VRF) 도달 불가"   wrk1  "ping -c1 -W1 10.10.24.31"
docker exec $N-inet sh -c "ip route replace 10.10.0.0/16 via 203.0.113.2" 2>/dev/null
ok "인터넷 → 공개 IP:80 → VIP (r10 + DNAT)"     inet  "curl -s --max-time 3 http://203.0.113.10/ | grep -q served-by"
ng "인터넷 → Ceph public 직접 (r92)"            inet  "ping -c1 -W1 10.10.20.31"
ng "인터넷 → 노드 SSH 직접 (기본 차단)"           inet  "nc -z -w2 10.10.16.21 22"
ng "인터넷 → 관리망 (r92)"                       inet  "ping -c1 -W1 10.10.10.10"
ok "노드 → 인터넷 80 (r20 + SNAT)"               wrk1  "wget -qO- -T 3 http://203.0.113.1/ | grep -q internet-ok"
ng "Ceph → 인터넷 (r91)"                         ceph1 "curl -s --max-time 3 http://203.0.113.1/"
docker exec -d $N-wrk1 sh -c "nc -lk -p 9100 -e echo up" 2>/dev/null
docker exec -d $N-wrk1 sh -c "nc -lk -p 23 -e echo up" 2>/dev/null
sleep 1
ok "배스천 → 노드 9100 node_exporter (r30)"      bastion "nc -z -w2 10.10.16.21 9100"
ng "배스천 → 노드 23 telnet (허용 목록 밖)"        bastion "nc -z -w2 10.10.16.21 23"
ok "배스천 → Ceph ping (r32)"                     bastion "ping -c2 -W1 10.10.20.31"

echo "== 4. K8s BGP 피어링 (VIP 광고, 경로 필터)"
ok "leaf1a가 VIP를 두 랙(R2·R3) 경유로 나눠 받음" leaf1a "[ \$(ip route show vrf PROD 10.10.32.10 | grep -c nexthop) -ge 2 ]"
ok "리프 해시 정책이 포트까지 봄 (policy 1)" leaf1a "[ \$(sysctl -n net.ipv4.fib_multipath_hash_policy) = 1 ]"
ok "leaf1a → leaf3a VTEP 경로 2갈래 (ECMP)" leaf1a "[ \$(ip route show 10.10.1.15 | grep -c nexthop) -ge 2 ]"
docker exec $N-wrk2 vtysh -c "conf t" -c "ip route 8.8.8.0/24 Null0" -c "router bgp 65200" \
  -c "address-family ipv4 unicast" -c "network 8.8.8.0/24" >/dev/null 2>&1
sleep 4
ng "노드가 엉뚱한 대역(8.8.8.0/24) 광고 → 리프가 거부" leaf1a "ip route show vrf PROD | grep -q 8.8.8.0"
docker exec $N-wrk2 vtysh -c "conf t" -c "router bgp 65200" -c "address-family ipv4 unicast" \
  -c "no network 8.8.8.0/24" -c "exit" -c "exit" -c "no ip route 8.8.8.0/24 Null0" >/dev/null 2>&1

if [ "${1:-}" = "quick" ]; then echo; echo "결과: PASS $PASS / FAIL $FAIL"; exit $FAIL; fi

echo "== 5. 장애 시험"
loss() {  # $1 장비 $2 목적지 $3 장애 명령 장비 $4 장애 명령 $5 복구 명령
  docker exec $N-$1 sh -c "ping -i 0.05 -c 200 -W1 $2 > /tmp/p.txt" &
  P=$!; sleep 2
  docker exec $N-$3 sh -c "$4"
  wait $P
  docker exec $N-$3 sh -c "$5"
  docker exec $N-$1 sh -c "grep -o '[0-9]* received' /tmp/p.txt | cut -d' ' -f1"
}
r=$(loss ctrl3 10.10.16.11 leaf3a "ip link set eth1 down" "ip link set eth1 up")
echo "      서버 쪽 링크 1줄 끊김 (ctrl3 bond0 활성 링크): 200개 중 ${r}개 수신"
[ "$r" -ge 180 ] && { echo "PASS  bond 절체 후 통신 유지"; PASS=$((PASS+1)); } || { echo "FAIL  bond 절체"; FAIL=$((FAIL+1)); }
sleep 3
r=$(loss ctrl3 10.10.16.11 spine1 "for i in 1 2 3 4 5 6; do ip link set eth\$i down; done" "for i in 1 2 3 4 5 6; do ip link set eth\$i up; done")
echo "      스파인 1대 전체 다운: 200개 중 ${r}개 수신"
[ "$r" -ge 180 ] && { echo "PASS  스파인 1대 장애에도 통신 유지"; PASS=$((PASS+1)); } || { echo "FAIL  스파인 장애"; FAIL=$((FAIL+1)); }
sleep 10
docker exec $N-wrk5 vtysh -c "conf t" -c "router bgp 65200" -c "address-family ipv4 unicast" -c "no network 10.10.32.10/32" >/dev/null
sleep 4
ok "VIP 노드 1대(wrk5) 빠짐 → 남은 wrk3이 응답" inet "curl -s --max-time 3 http://203.0.113.10/ | grep -q 'served-by wrk3'"
docker exec $N-wrk5 vtysh -c "conf t" -c "router bgp 65200" -c "address-family ipv4 unicast" -c "network 10.10.32.10/32" >/dev/null

echo; echo "결과: PASS $PASS / FAIL $FAIL"
exit $FAIL
