# 대역폭·가용성 점검 (자동 생성)

| 리프 | 서버 포트 | 하향 합계 | 상향 합계 | 과가입비 |
|---|---|---|---|---|
| leaf1a | 9 | 235G | 200G | 1.18 : 1 |
| leaf1b | 9 | 235G | 200G | 1.18 : 1 |
| leaf2a | 7 | 185G | 200G | 0.93 : 1 |
| leaf2b | 7 | 185G | 200G | 0.93 : 1 |
| leaf3a | 5 | 125G | 200G | 0.62 : 1 |
| leaf3b | 5 | 125G | 200G | 0.62 : 1 |

| 랙 | 서버 | 스위치·방화벽 | 추정 전력 |
|---|---|---|---|
| R1 | ctrl1, wrk1, wrk2, ceph1, ceph2 | spine1, leaf1a, leaf1b, oob1, fw1 | 6.1 / 8 kW |
| R2 | ctrl2, wrk3, wrk4, ceph3 | leaf2a, leaf2b, oob2, fw2 | 4.9 / 8 kW |
| R3 | ctrl3, wrk5, ceph4 | spine2, leaf3a, leaf3b, oob3 | 4.2 / 8 kW |

| 케이블 종류 | 속도 | 수량 |
|---|---|---|
| fabric | 100G | 12 |
| firewall | 10G | 4 |
| fw-ha | 10G | 1 |
| isp | 10G | 2 |
| oob | 1G | 22 |
| oob-uplink | 10G | 4 |
| server | 25G | 42 |
