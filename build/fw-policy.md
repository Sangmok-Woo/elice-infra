| ID | 출발 | 도착 | 대상 | 프로토콜/포트 | 동작 | 설명 |
|---|---|---|---|---|---|---|
| 10 | outside | prod | lb_vip | tcp 80,443 | accept | 서비스 공개는 VIP의 80/443만 |
| 20 | prod | outside | any | tcp 80,443 | accept | 레지스트리, 패키지 저장소 |
| 21 | prod | outside | any | udp 53,123 | accept | DNS, NTP |
| 30 | mgmt | prod | any | tcp 22,6443,9100,9115,10250 | accept | SSH, kube-apiserver, node_exporter, 경로 프로브, kubelet |
| 31 | mgmt | storage | any | tcp 22,9100,9283 | accept | SSH, node_exporter, ceph-mgr exporter |
| 32 | mgmt | prod, storage | any | icmp | accept | 진단용 ping |
| 90 | prod | storage | any | any | drop +log | 스토리지는 전용 다리(VLAN 120)로만 붙는다 |
| 91 | storage | outside | any | any | drop +log | 스토리지 노드는 인터넷에 직접 나가지 않는다 |
| 92 | outside | storage, mgmt | any | any | drop +log | 외부에서 내부 관리·스토리지 직접 접근 차단 |
| - | any | any | any | any | drop | 기본 정책 |
