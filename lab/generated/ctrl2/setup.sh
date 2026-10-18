#!/bin/sh
# ctrl2 (controller) 랩용 NIC 설정. 생성물
# 운영 서버는 LACP(802.3ad) + 리프 ESI-LAG. 랩 리프는 ESI를 흉내 내지 않으므로 active-backup으로 대신한다
set -e
ip link add bond0 type bond mode active-backup miimon 100
ip link set eth1 down
ip link set eth1 master bond0
ip link set eth2 down
ip link set eth2 master bond0
ip link set bond0 mtu 9000 up
ip link add link bond0 name bond0.110 type vlan id 110
ip link set bond0.110 mtu 1500 up
ip addr add 10.10.16.12/24 dev bond0.110
ip link add link bond0 name bond0.120 type vlan id 120
ip link set bond0.120 mtu 9000 up
ip addr add 10.10.20.12/24 dev bond0.120
ip route del default 2>/dev/null || true
ip route add default via 10.10.16.1
