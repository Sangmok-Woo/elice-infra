# elice-infra

엘리스 인프라 네트워크 엔지니어 미니프로젝트 제출물.
Kubernetes(컨트롤러 3, 워커 5)와 Ceph(4대)를 3개 랙에 수용하는 데이터센터 네트워크 설계와 운영 자동화.

- 제안서: [elice-infra-proposal.pdf](elice-infra-proposal.pdf) (원본 `proposal/proposal.html`)
- 설계 원장: [inventory/site.yml](inventory/site.yml), [inventory/fw-policy.yml](inventory/fw-policy.yml)

## 구조

```
inventory/        설계 원장 (사람이 고치는 곳은 여기뿐)
tools/netgen.py   원장 점검 + 생성기
templates/        Jinja2 템플릿 (스위치 FRR, 리프 데이터플레인, netplan, 랩)
build/            생성물: 스위치 설정, 서버 netplan, IP 표, 배선표, NetBox CSV, 모니터링 대상, Ansible 인벤토리
lab/              containerlab 검증 랩 (생성된 설정을 그대로 올림)
automation/       Ansible: push-config.yml(배포), check-fabric.yml(점검)
monitoring/       Prometheus 수집 설정, 알람 규칙
proposal/         제안서 HTML
```

## 실행

생성 (Windows/Linux, Python 3.11, `pip install pyyaml jinja2`)

```bash
python tools/netgen.py validate
python tools/netgen.py build
```

랩 (WSL Ubuntu, containerlab 0.75, 이미지: `quay.io/frrouting/frr:10.2.1`, `hospnet-host`, `fwlab-fw`)

```bash
cd lab && ./deploy.sh && ./test.sh
./destroy.sh
```

`test.sh quick`은 장애 시험을 뺀 29항목만 돈다. 컨테이너 23개, 메모리 약 1GB.

Ansible (랩 대상)

```bash
cd automation/ansible
ansible-playbook -i ../../lab/generated/ansible-hosts.yml check-fabric.yml
ansible-playbook -i ../../lab/generated/ansible-hosts.yml push-config.yml --check --diff -l leaf3a -e config_dir=$PWD/../../lab/generated
```

## 검증 결과 (2026-10-17)

- `test.sh`: PASS 32 / FAIL 0. 서버 링크 단절 손실 2/200, 스파인 1대 다운 손실 0/200
- `check-fabric.yml`: BGP 세션·VTEP·ECMP 2갈래 확인. spine1 eth5 다운 시 끊긴 포트와 ECMP가 한 갈래가 된 리프를 지목
- `push-config.yml`: 차이 미리보기, 적용, 원복, 문법 오류 차단 확인

## 랩과 운영 설계의 차이

| 항목 | 운영 설계 | 랩 |
|---|---|---|
| 포트 이름 | EthernetN, ens{n}f{m} | ethN |
| 서버 bond | LACP + 리프 ESI-LAG | active-backup |
| 방화벽 | NGFW HA 쌍 | fw1 한 대, nftables |
| OOB, ISP | 있음 | 없음 (인터넷 사용자·배스천 컨테이너로 대체) |
| QoS | 설계만 | 미적용 |

## 함정

- `fwlab-fw` 이미지는 기본 엔트리포인트가 없는 nft 파일을 읽다 죽는다. 토폴로지에서 `entrypoint: sleep infinity`로 덮었다.
- FRR이 VRF 장치보다 먼저 뜬다. `deploy.sh`가 `interfaces.sh` 후 `vtysh -f`로 설정을 한 번 더 읽힌다.
- 랩 컨테이너의 `/etc/frr/frr.conf`는 바인드 마운트라 Ansible copy의 원자적 교체가 실패한다. `unsafe_writes: true`.
- `frr-reload.py --test`는 변경이 없어도 정규화 차이를 출력해서 변경 감지에 쓸 수 없다. 파일 diff로 감지하고 reload는 적용에만 쓴다.
- WSL에서 실행할 때는 배포판이 꺼지지 않게 `wsl -d Ubuntu -u root -- sleep infinity`를 하나 띄워 둔다.
