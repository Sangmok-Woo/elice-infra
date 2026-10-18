#!/usr/bin/env bash
# 축소 검증 랩 배포: 스위치 8 + 서버 12 + 방화벽 1 + 인터넷/배스천
# 사용법: ./deploy.sh   (WSL 안에서, lab/ 디렉터리 기준)
set -eu
cd "$(dirname "$0")"
N=clab-elice
LEAVES="leaf1a leaf1b leaf2a leaf2b leaf3a leaf3b"
SERVERS="ctrl1 ctrl2 ctrl3 wrk1 wrk2 wrk3 wrk4 wrk5 ceph1 ceph2 ceph3 ceph4"

containerlab destroy -t elice.clab.yml --cleanup >/dev/null 2>&1 || true
containerlab deploy -t elice.clab.yml >/dev/null
echo "== 리프 데이터플레인 (VRF/VXLAN/브리지)"
for l in $LEAVES; do docker exec $N-$l sh /interfaces.sh; done
# FRR은 VRF 장치보다 먼저 떴다. VRF가 생긴 뒤 설정을 한 번 더 읽힌다
for l in $LEAVES; do docker exec $N-$l vtysh -f /etc/frr/frr.conf >/dev/null 2>&1 || true; done
echo "== 서버 NIC (bond + VLAN)"
for s in $SERVERS; do docker exec $N-$s sh /setup.sh; done
echo "== 방화벽, 인터넷, 배스천"
docker exec $N-fw1 sh /setup.sh
docker exec $N-inet sh -c 'ip addr add 203.0.113.1/28 dev eth1; ip link set eth1 up
  (while true; do printf "HTTP/1.0 200 OK\r\n\r\ninternet-ok\n" | nc -l -p 80 -q 1 >/dev/null 2>&1; done) &'
docker exec $N-bastion sh -c 'ip addr add 10.10.10.10/24 dev eth1; ip link set eth1 up
  ip route del default; ip route add default via 10.10.10.1'
echo "== BGP 수렴 대기"
sleep 20
echo "배포 완료. 점검: ./test.sh"
