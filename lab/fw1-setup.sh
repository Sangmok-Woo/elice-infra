#!/bin/sh
# fw1: 경계 리프 두 대(leaf1a/leaf1b eth47)로 VRF별 transit VLAN을 받는다
#   fw0.901 = PROD, fw0.902 = STORAGE, eth3 = 바깥(인터넷), eth4 = 관리 존
# 운영에서는 NGFW HA 쌍이며 .1은 두 장비가 나눠 갖는 가상 IP다
set -e
sysctl -qw net.ipv4.ip_forward=1 net.ipv4.conf.all.rp_filter=0 net.ipv4.conf.default.rp_filter=0
ip link add fw0 type bond mode active-backup miimon 100
for p in eth1 eth2; do ip link set $p down; ip link set $p master fw0; done
ip link set fw0 up
ip link add link fw0 name fw0.901 type vlan id 901
ip link add link fw0 name fw0.902 type vlan id 902
ip link set fw0.901 up; ip link set fw0.902 up
ip addr add 10.10.250.1/29 dev fw0.901
ip addr add 10.10.250.9/29 dev fw0.902
ip link set eth3 up; ip addr add 203.0.113.2/28 dev eth3
ip addr add 203.0.113.10/32 dev eth3          # 서비스 공개 IP (DNAT 대상)
ip link set eth4 up; ip addr add 10.10.10.1/24 dev eth4
ip route del default 2>/dev/null || true
ip route add default via 203.0.113.1
# 내부 대역 → 해당 VRF의 경계 리프 애니캐스트 게이트웨이(.2)
for n in 10.10.16.0/20 10.10.32.0/24; do ip route add $n via 10.10.250.2; done
ip route add 10.10.20.0/24 via 10.10.250.10
nft -f /rules.nft
echo "fw1 ready"
