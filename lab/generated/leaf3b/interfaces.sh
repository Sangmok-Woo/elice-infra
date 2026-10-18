#!/bin/sh
# leaf3b 리눅스 데이터플레인 (VRF, L3VNI, VLAN 브리지, VXLAN). 생성물
# SONiC 장비에서는 같은 내용을 config_db.json(VRF/VLAN/VXLAN_TUNNEL_MAP)으로 넣는다
set -e
sysctl -qw net.ipv4.ip_forward=1 net.ipv6.conf.all.forwarding=1 net.ipv4.conf.all.rp_filter=0 net.ipv4.conf.default.rp_filter=0
# ECMP 해시가 포트까지 보게 한다. 기본값(0)은 주소만 봐서 흐름이 한 스파인으로 쏠린다 (제안서 3-6)
sysctl -qw net.ipv4.fib_multipath_hash_policy=1
ip link set eth49 mtu 9216 up
ip link set eth50 mtu 9216 up

# VRF마다 L3VNI 하나 (대칭형 IRB: 다른 서브넷 사이 라우팅은 이 VNI로 넘어간다)
ip link add PROD type vrf table 10
ip link set PROD up
ip link add br50010 type bridge stp_state 0
ip link set br50010 master PROD addrgenmode none
ip link add vx50010 type vxlan id 50010 local 10.10.1.16 dstport 4789 nolearning
ip link set vx50010 master br50010 mtu 9000 addrgenmode none
ip link set vx50010 up
ip link set br50010 mtu 9000 up
ip link add STORAGE type vrf table 20
ip link set STORAGE up
ip link add br50020 type bridge stp_state 0
ip link set br50020 master STORAGE addrgenmode none
ip link add vx50020 type vxlan id 50020 local 10.10.1.16 dstport 4789 nolearning
ip link set vx50020 master br50020 mtu 9000 addrgenmode none
ip link set vx50020 up
ip link set br50020 mtu 9000 up
ip link add REPL type vrf table 30
ip link set REPL up
ip link add br50030 type bridge stp_state 0
ip link set br50030 master REPL addrgenmode none
ip link add vx50030 type vxlan id 50030 local 10.10.1.16 dstport 4789 nolearning
ip link set vx50030 master br50030 mtu 9000 addrgenmode none
ip link set vx50030 up
ip link set br50030 mtu 9000 up

# VLAN마다 브리지 + L2VNI + 애니캐스트 게이트웨이 (모든 리프가 같은 IP/MAC)
ip link add br110 type bridge stp_state 0
ip link set br110 address 00:00:5e:00:01:01 master PROD
ip link add vx10110 type vxlan id 10110 local 10.10.1.16 dstport 4789 nolearning
ip link set vx10110 mtu 1500 master br110 addrgenmode none
bridge link set dev vx10110 neigh_suppress on learning off
ip link set vx10110 up
for p in eth1 eth2; do
  [ -e /sys/class/net/$p ] || continue      # 랩에서 생략한 장비(fw2 등) 쪽 포트
  ip link set $p mtu 9216 up
  ip link add link $p name $p.110 type vlan id 110
  ip link set $p.110 mtu 1500 master br110 up
done
ip link set br110 mtu 1500 up
ip addr add 10.10.16.1/24 dev br110
ip link add br120 type bridge stp_state 0
ip link set br120 address 00:00:5e:00:01:01 master STORAGE
ip link add vx10120 type vxlan id 10120 local 10.10.1.16 dstport 4789 nolearning
ip link set vx10120 mtu 9000 master br120 addrgenmode none
bridge link set dev vx10120 neigh_suppress on learning off
ip link set vx10120 up
for p in eth1 eth3 eth4; do
  [ -e /sys/class/net/$p ] || continue      # 랩에서 생략한 장비(fw2 등) 쪽 포트
  ip link set $p mtu 9216 up
  ip link add link $p name $p.120 type vlan id 120
  ip link set $p.120 mtu 9000 master br120 up
done
ip link set br120 mtu 9000 up
ip addr add 10.10.20.1/24 dev br120
ip link add br130 type bridge stp_state 0
ip link set br130 address 00:00:5e:00:01:01 master REPL
ip link add vx10130 type vxlan id 10130 local 10.10.1.16 dstport 4789 nolearning
ip link set vx10130 mtu 9000 master br130 addrgenmode none
bridge link set dev vx10130 neigh_suppress on learning off
ip link set vx10130 up
for p in eth5; do
  [ -e /sys/class/net/$p ] || continue      # 랩에서 생략한 장비(fw2 등) 쪽 포트
  ip link set $p mtu 9216 up
  ip link add link $p name $p.130 type vlan id 130
  ip link set $p.130 mtu 9000 master br130 up
done
ip link set br130 mtu 9000 up
ip addr add 10.10.24.1/24 dev br130
echo "leaf3b: VRF 3개, VLAN 110,120,130"
