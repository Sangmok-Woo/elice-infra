#!/usr/bin/env python3
"""사이트 원장(inventory/site.yml)에서 모든 산출물을 만든다.

  python tools/netgen.py validate   원장 점검만
  python tools/netgen.py build      점검 + build/ 아래에 전부 생성

생성물
  build/ipam.csv, ipam.md            IP 할당표
  build/cabling.csv                  배선표 (케이블 라벨 포함)
  build/netbox/*.csv                 NetBox 일괄 입력용
  build/configs/<sw>/frr.conf        스위치 설정 (운영 포트명 EthernetN)
  build/servers/<srv>.netplan.yaml   서버 NIC 설정 (LACP)
  build/monitoring/*.json            Prometheus 수집 대상
  build/ansible/hosts.yml            Ansible 인벤토리
  build/report.md                    대역폭·용량 점검 결과
  lab/                               containerlab 축소 검증용 (포트명 ethN)
"""
import csv
import ipaddress as ipa
import json
import sys
from collections import defaultdict
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
LAB = ROOT / "lab"

LEAF_DOWN_PORTS = 46      # 1~46: 서버용 25G
LEAF_FW_PORT = 47         # 47: 방화벽 내부 다리
LEAF_UP_FIRST = 49        # 49~56: 100G 업링크


def net(p):
    return ipa.ip_network(p)


def host(prefix, n):
    return str(net(prefix).network_address + n)


class Site:
    def __init__(self, path):
        self.y = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        y = self.y
        self.sw = {s["name"]: s for s in y["switches"]}
        self.spines = [s for s in y["switches"] if s["role"] == "spine"]
        self.leaves = [s for s in y["switches"] if s["role"] == "leaf"]
        self.oobs = {s["rack"]: s for s in y["switches"] if s["role"] == "oob"}
        self.vlans = {v["id"]: v for v in y["vlans"]}
        self.vrfs = {v["name"]: v for v in y["vrfs"]}
        self.transit = {t["vrf"]: t for t in y["fw_transit"]}
        self.rack_pair = defaultdict(list)
        for l in self.leaves:
            self.rack_pair[l["rack"]].append(l["name"])
        self.links = []           # 배선 한 줄 = 케이블 하나
        self.ports = defaultdict(dict)   # 장비 -> 포트 -> 정보
        self.ips = []             # IP 할당표
        self.errors = []
        self._wire()
        self._address()

    # ---------- 배선 ----------
    def _link(self, a, ap, b, bp, speed, kind, **kw):
        for d, p in ((a, ap), (b, bp)):
            if p in self.ports[d]:
                self.errors.append(f"포트 중복: {d} {p}")
        self.ports[a][ap] = dict(peer=b, peer_port=bp, speed=speed, kind=kind, **kw)
        self.ports[b][bp] = dict(peer=a, peer_port=ap, speed=speed, kind=kind, **kw)
        self.links.append(dict(a=a, a_port=ap, b=b, b_port=bp, speed=speed, kind=kind, **kw))

    def _wire(self):
        y = self.y
        n_up = y["uplinks_per_spine"]
        # 리프 ↔ 스파인
        for li, leaf in enumerate(self.leaves):
            k = 0
            for si, sp in enumerate(self.spines):
                for j in range(n_up):
                    lp = LEAF_UP_FIRST + k
                    spp = 1 + li * n_up + j
                    self._link(leaf["name"], f"Ethernet{lp}", sp["name"], f"Ethernet{spp}",
                               y["uplink_speed"], "fabric")
                    k += 1
        # 서버 ↔ 리프 (bond 링크를 리프 a/b에 번갈아)
        nextp = defaultdict(lambda: 1)
        for s in y["servers"]:
            pair = self.rack_pair[s["rack"]]
            if len(pair) != 2:
                self.errors.append(f"{s['rack']} 리프가 2대가 아님")
                continue
            for bi, b in enumerate(y["nic_profiles"][s["role"]]):
                for i in range(b["links"]):
                    leaf = pair[i % 2]
                    p = nextp[leaf]
                    nextp[leaf] += 1
                    if p > LEAF_DOWN_PORTS:
                        self.errors.append(f"{leaf} 서버 포트 부족")
                    self._link(s["name"], f"ens{bi + 1}f{i}", leaf, f"Ethernet{p}",
                               b["speed"], "server", bond=b["bond"], vlans=b["vlans"])
        # 방화벽 ↔ 경계 리프 (VRF별 transit VLAN을 트렁크로)
        tv = [t["vlan"] for t in y["fw_transit"]]
        for fw in y["firewalls"]:
            for i, leaf in enumerate(fw["border_leaves"]):
                self._link(fw["name"], f"port{i + 1}", leaf, f"Ethernet{LEAF_FW_PORT}",
                           10, "firewall", bond="ae1", vlans=tv)
        f1, f2 = (f["name"] for f in y["firewalls"])
        self._link(f1, "ha1", f2, "ha1", 10, "fw-ha")
        self._link(f1, "wan1", "ISP-A", "handoff", 10, "isp")
        self._link(f2, "wan1", "ISP-B", "handoff", 10, "isp")
        # OOB: 모든 관리 포트·BMC는 같은 랙의 OOB 스위치로
        onext = defaultdict(lambda: 1)

        def oob(dev, port, rack):
            o = self.oobs[rack]["name"]
            p = onext[o]
            onext[o] += 1
            self._link(dev, port, o, f"Gi{p}", 1, "oob")
        for s in self.spines + self.leaves:
            oob(s["name"], "mgmt0", s["rack"])
        for fw in y["firewalls"]:
            oob(fw["name"], "mgmt", fw["rack"])
        for s in y["servers"]:
            oob(s["name"], "bmc", s["rack"])
        racks = [r["name"] for r in y["racks"]]
        # OOB 스위치끼리는 10G로 이어 관리 VLAN 하나로 묶고, 양 끝을 방화벽 mgmt 존에
        for a, b in zip(racks, racks[1:]):
            self._link(self.oobs[a]["name"], "Te2", self.oobs[b]["name"], "Te1", 10, "oob-uplink")
        self._link(self.oobs[racks[0]]["name"], "Te4", f1, "mgmt-zone", 10, "oob-uplink")
        self._link(self.oobs[racks[1]]["name"], "Te4", f2, "mgmt-zone", 10, "oob-uplink")

    # ---------- IP ----------
    def _ip(self, prefix, addr, dev, ifname, purpose, vrf="", vlan=""):
        self.ips.append(dict(prefix=prefix, address=addr, device=dev, interface=ifname,
                             purpose=purpose, vrf=vrf, vlan=vlan))

    def lo(self, sw):
        p = self.y["prefixes"]
        base = 0 if sw["role"] == "spine" else 10
        return host(p["loopback"], base + sw["idx"]), host(p["vtep"], base + sw["idx"])

    def asn(self, sw):
        a = self.y["site"]["asn_base"]
        return a["spine"] if sw["role"] == "spine" else a["leaf"] + sw["idx"]

    def _address(self):
        y, p = self.y, self.y["prefixes"]
        for s in self.spines + self.leaves:
            rid, vtep = self.lo(s)
            self._ip(p["loopback"], rid + "/32", s["name"], "lo", "router-id")
            if s["role"] == "leaf":
                self._ip(p["vtep"], vtep + "/32", s["name"], "lo", "vtep")
        oob_n = {"spine": 0, "leaf": 10, "oob": 20}
        for s in y["switches"]:
            self._ip(p["oob_net"], host(p["oob_net"], oob_n[s["role"]] + s["idx"]) + "/24",
                     s["name"], "mgmt0", "oob")
        for i, fw in enumerate(y["firewalls"]):
            self._ip(p["oob_net"], host(p["oob_net"], 31 + i) + "/24", fw["name"], "mgmt", "oob")
        for v in y["vlans"]:
            plen = net(v["prefix"]).prefixlen
            self._ip(v["prefix"], f"{host(v['prefix'], 1)}/{plen}", "leaf-*", f"vlan{v['id']}",
                     "anycast-gw", v["vrf"], v["id"])
        for t in y["fw_transit"]:
            plen = net(t["prefix"]).prefixlen
            self._ip(t["prefix"], f"{host(t['prefix'], 1)}/{plen}", "fw-ha", f"vlan{t['vlan']}",
                     "fw-vip", t["vrf"], t["vlan"])
            self._ip(t["prefix"], f"{host(t['prefix'], 2)}/{plen}", "border-leaf-*",
                     f"vlan{t['vlan']}", "anycast-gw", t["vrf"], t["vlan"])
        for s in y["servers"]:
            self._ip(p["oob_bmc"], host(p["oob_bmc"], s["host"]) + "/24", s["name"], "bmc", "bmc")
            for b in y["nic_profiles"][s["role"]]:
                for vid in b["vlans"]:
                    v = self.vlans[vid]
                    plen = net(v["prefix"]).prefixlen
                    self._ip(v["prefix"], f"{host(v['prefix'], s['host'])}/{plen}", s["name"],
                             f"{b['bond']}.{vid}", v["name"], v["vrf"], vid)
            if s["role"] in ("controller", "worker"):
                pod = net(p["pod"])
                self._ip(p["pod"], str(list(pod.subnets(new_prefix=24))[s["host"]]), s["name"],
                         "cilium", "pod-cidr", "PROD")

    # ---------- 점검 ----------
    def validate(self):
        y, p = self.y, self.y["prefixes"]
        err = list(self.errors)
        # 1) 주소 중복
        seen = {}
        for r in self.ips:
            if "-*" in r["device"] or r["device"] == "fw-ha":
                continue
            a = r["address"].split("/")[0]
            if a in seen:
                err.append(f"IP 중복: {a} ({seen[a]} / {r['device']})")
            seen[a] = r["device"]
        # 2) 할당 주소는 선언한 대역 안에
        for r in self.ips:
            if ipa.ip_interface(r["address"]).ip not in net(r["prefix"]):
                err.append(f"대역 밖 주소: {r['address']} ∉ {r['prefix']}")
        # 3) 최상위 대역끼리 겹침 (사이트 대역과 그 하위는 제외)
        tops = {k: net(v) for k, v in p.items() if k not in ("site",)}
        keys = list(tops)
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                if tops[a].overlaps(tops[b]):
                    err.append(f"대역 겹침: {a} {tops[a]} / {b} {tops[b]}")
        for v in y["vlans"]:
            for k, t in tops.items():
                if net(v["prefix"]).overlaps(t) and k != "site":
                    err.append(f"VLAN {v['id']} 대역이 {k}와 겹침")
        # 4) 가용성: 컨트롤러는 랙마다 하나, 서버마다 bond 링크가 리프 2대로 갈라지는지
        crack = [s["rack"] for s in y["servers"] if s["role"] == "controller"]
        if len(set(crack)) != len(crack):
            err.append("컨트롤러 둘이 같은 랙에 있음 (랙 하나 장애로 etcd 정족수 상실)")
        for s in y["servers"]:
            for b in y["nic_profiles"][s["role"]]:
                peers = {i["peer"] for i in self.ports[s["name"]].values() if i.get("bond") == b["bond"]}
                if len(peers) < 2:
                    err.append(f"{s['name']} {b['bond']} 단일 리프에 몰림")
        # 4-1) 서버 끝자리 규칙, Ingress 노드는 워커이고 서로 다른 랙
        for s in y["servers"]:
            lo, hi = y["host_ranges"][s["role"]]
            if not lo <= s["host"] <= hi:
                err.append(f"{s['name']} 끝자리 .{s['host']}가 {s['role']} 범위(.{lo}~.{hi}) 밖")
        ing = [x for x in y["servers"] if x["name"] in y["services"]["ingress_nodes"]]
        if any(x["role"] != "worker" for x in ing):
            err.append("Ingress 노드는 워커여야 함")
        if len({x["rack"] for x in ing}) < 2:
            err.append("Ingress 노드가 한 랙에 몰림 (랙 장애 시 VIP 상실)")
        if not net(y["prefixes"]["lb_vip"]).supernet_of(net(y["services"]["web_vip"] + "/32")):
            err.append("web_vip가 lb_vip 대역 밖")
        # 5) 스파인이 서로 다른 랙
        if len({s["rack"] for s in self.spines}) < len(self.spines):
            err.append("스파인이 같은 랙에 있음")
        # 6) 랙 전력 (서버 0.8kW, GPU 노드는 따로 계산)
        for r in y["racks"]:
            kw = 0.8 * sum(1 for s in y["servers"] if s["rack"] == r["name"]) \
                + 0.45 * sum(1 for s in y["switches"] if s["rack"] == r["name"]) \
                + 0.3 * sum(1 for f in y["firewalls"] if f["rack"] == r["name"])
            if kw > r["power_kw"] * 0.8:
                err.append(f"{r['name']} 전력 {kw:.1f}kW, 한도 80% 초과")
        return err

    # ---------- 대역폭 보고 ----------
    def report(self):
        y = self.y
        out = ["# 대역폭·가용성 점검 (자동 생성)\n",
               "| 리프 | 서버 포트 | 하향 합계 | 상향 합계 | 과가입비 |", "|---|---|---|---|---|"]
        for l in self.leaves:
            ps = self.ports[l["name"]].values()
            down = sum(i["speed"] for i in ps if i["kind"] in ("server", "firewall"))
            up = sum(i["speed"] for i in ps if i["kind"] == "fabric")
            n = sum(1 for i in ps if i["kind"] == "server")
            out.append(f"| {l['name']} | {n} | {down}G | {up}G | {down / up:.2f} : 1 |")
        out += ["", "| 랙 | 서버 | 스위치·방화벽 | 추정 전력 |", "|---|---|---|---|"]
        for r in y["racks"]:
            sv = [s["name"] for s in y["servers"] if s["rack"] == r["name"]]
            nw = [s["name"] for s in y["switches"] + y["firewalls"] if s["rack"] == r["name"]]
            kw = 0.8 * len(sv) + 0.45 * sum(1 for s in y["switches"] if s["rack"] == r["name"]) \
                + 0.3 * sum(1 for f in y["firewalls"] if f["rack"] == r["name"])
            out.append(f"| {r['name']} | {', '.join(sv)} | {', '.join(nw)} | {kw:.1f} / {r['power_kw']} kW |")
        kinds = defaultdict(int)
        for k in self.links:
            kinds[(k["kind"], k["speed"])] += 1
        out += ["", "| 케이블 종류 | 속도 | 수량 |", "|---|---|---|"]
        for (k, s), n in sorted(kinds.items()):
            out.append(f"| {k} | {s}G | {n} |")
        return "\n".join(out) + "\n"

    # ---------- 템플릿 컨텍스트 ----------
    def leaf_ctx(self, leaf, portmap=lambda p: p):
        y = self.y
        rid, vtep = self.lo(leaf)
        ps = self.ports[leaf["name"]]
        trunks = defaultdict(list)          # vlan -> [포트]
        for port, i in sorted(ps.items(), key=lambda kv: int(''.join(c for c in kv[0] if c.isdigit()) or 0)):
            for v in i.get("vlans", []):
                trunks[v].append(portmap(port))
        vlans = []
        for vid in sorted(trunks):
            if vid in self.vlans:
                v = self.vlans[vid]
                gw = host(v["prefix"], 1)
                mtu = v["mtu"]
                vrf = v["vrf"]
                pfx = v["prefix"]
            else:
                t = next(t for t in y["fw_transit"] if t["vlan"] == vid)
                gw, mtu, vrf, pfx = host(t["prefix"], 2), y["site"]["mtu"]["front"], t["vrf"], t["prefix"]
            vlans.append(dict(id=vid, vni=10000 + vid, vrf=vrf, gw=gw, plen=net(pfx).prefixlen,
                              mtu=mtu, ports=trunks[vid]))
        uplinks = [portmap(p) for p, i in sorted(ps.items()) if i["kind"] == "fabric"]
        return dict(name=leaf["name"], asn=self.asn(leaf), rid=rid, vtep=vtep, uplinks=uplinks,
                    vlans=vlans, vrfs=y["vrfs"], border=leaf.get("border", False),
                    transit=[dict(vrf=t["vrf"], nh=host(t["prefix"], 1)) for t in y["fw_transit"]],
                    k8s_asn=y["site"]["asn_base"]["k8s"], rt=y["site"]["rt_admin"],
                    k8s_net=self.vlans[110]["prefix"], lb_vip=y["prefixes"]["lb_vip"],
                    pod=y["prefixes"]["pod"], mac=y["site"]["anycast_gw_mac"],
                    fabric_mtu=y["site"]["mtu"]["fabric"])

    def spine_ctx(self, sp, portmap=lambda p: p):
        rid, _ = self.lo(sp)
        ps = self.ports[sp["name"]]
        down = [portmap(p) for p, i in sorted(ps.items(), key=lambda kv: int(kv[0][8:]) if kv[0].startswith("Ethernet") else 0)
                if i["kind"] == "fabric"]
        return dict(name=sp["name"], asn=self.asn(sp), rid=rid, downlinks=down,
                    fabric_mtu=self.y["site"]["mtu"]["fabric"])

    def server_ctx(self, s):
        y = self.y
        bonds = []
        for b in y["nic_profiles"][s["role"]]:
            nics = sorted(p for p, i in self.ports[s["name"]].items() if i.get("bond") == b["bond"])
            vl = []
            for vid in b["vlans"]:
                v = self.vlans[vid]
                vl.append(dict(id=vid, addr=f"{host(v['prefix'], s['host'])}/{net(v['prefix']).prefixlen}",
                               mtu=v["mtu"], gw=host(v["prefix"], 1), name=v["name"]))
            bonds.append(dict(name=b["bond"], nics=nics, vlans=vl, mtu=max(x["mtu"] for x in vl)))
        # 기본 경로: K8s 노드는 VLAN 110, Ceph는 VLAN 120
        gwvlan = 110 if s["role"] != "ceph" else 120
        return dict(name=s["name"], role=s["role"], bonds=bonds, default_vlan=gwvlan,
                    gw=host(self.vlans[gwvlan]["prefix"], 1), host=s["host"])


def env():
    return Environment(loader=FileSystemLoader(ROOT / "templates"), undefined=StrictUndefined,
                       trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True)


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def label(link):
    return f"{link['a']}:{link['a_port']}<>{link['b']}:{link['b_port']}"


def build(site):
    y, e = site.y, env()
    # IP 표
    write_csv(BUILD / "ipam.csv", site.ips, ["prefix", "address", "device", "interface", "purpose", "vrf", "vlan"])
    md = ["| 대역 | 주소 | 장비 | 인터페이스 | 용도 | VRF | VLAN |", "|---|---|---|---|---|---|---|"]
    md += [f"| {r['prefix']} | {r['address']} | {r['device']} | {r['interface']} | {r['purpose']} | {r['vrf']} | {r['vlan']} |" for r in site.ips]
    write(BUILD / "ipam.md", "\n".join(md) + "\n")
    # 배선표
    rack_of = {d["name"]: d["rack"] for d in y["switches"] + y["servers"] + y["firewalls"]}
    media = {100: "QSFP28 AOC/SR4", 25: "SFP28 DAC(랙내)/AOC", 10: "SFP+ DAC", 1: "Cat6 UTP"}
    rows = []
    for i, k in enumerate(site.links, 1):
        ra, rb = rack_of.get(k["a"], "-"), rack_of.get(k["b"], "-")
        m = media[k["speed"]]
        if k["speed"] == 25 and ra == rb:
            m = "SFP28 DAC"
        rows.append(dict(id=f"C{i:03d}", a_rack=ra, a=k["a"], a_port=k["a_port"], b_rack=rb, b=k["b"],
                         b_port=k["b_port"], speed=f"{k['speed']}G", media=m, kind=k["kind"],
                         label=label(k)))
    write_csv(BUILD / "cabling.csv", rows, list(rows[0]))
    # NetBox 일괄 입력 (DCIM > Import 화면 컬럼명)
    nb = BUILD / "netbox"
    devs = [dict(name=s["name"], role=s["role"], site=y["site"]["name"], rack=s["rack"],
                 device_type=s["model"], status="planned") for s in y["switches"]]
    devs += [dict(name=f["name"], role="firewall", site=y["site"]["name"], rack=f["rack"],
                  device_type=f["model"], status="planned") for f in y["firewalls"]]
    devs += [dict(name=s["name"], role=s["role"], site=y["site"]["name"], rack=s["rack"],
                  device_type="1U server", status="planned") for s in y["servers"]]
    write_csv(nb / "devices.csv", devs, ["name", "role", "site", "rack", "device_type", "status"])
    cab = [dict(side_a_device=k["a"], side_a_type="dcim.interface", side_a_name=k["a_port"],
                side_b_device=k["b"], side_b_type="dcim.interface", side_b_name=k["b_port"],
                label=r["id"], status="planned") for k, r in zip(site.links, rows) if not k["b"].startswith("ISP")]
    write_csv(nb / "cables.csv", cab, list(cab[0]))
    pf = [dict(prefix=v, description=k, status="active") for k, v in y["prefixes"].items()]
    pf += [dict(prefix=v["prefix"], vrf=v["vrf"], vlan=v["id"], description=v["name"], status="active") for v in y["vlans"]]
    write_csv(nb / "prefixes.csv", pf, ["prefix", "vrf", "vlan", "description", "status"])
    ipr = [dict(address=r["address"], vrf=r["vrf"], device=r["device"], interface=r["interface"],
                description=r["purpose"], status="active") for r in site.ips if "*" not in r["device"]]
    write_csv(nb / "ip-addresses.csv", ipr, ["address", "vrf", "device", "interface", "description", "status"])
    # 스위치 설정
    for l in site.leaves:
        write(BUILD / "configs" / l["name"] / "frr.conf", e.get_template("leaf-frr.j2").render(**site.leaf_ctx(l)))
        write(BUILD / "configs" / l["name"] / "interfaces.sh", e.get_template("leaf-if.sh.j2").render(**site.leaf_ctx(l)))
    for s in site.spines:
        write(BUILD / "configs" / s["name"] / "frr.conf", e.get_template("spine-frr.j2").render(**site.spine_ctx(s)))
    for s in y["servers"]:
        write(BUILD / "servers" / f"{s['name']}.netplan.yaml", e.get_template("netplan.j2").render(**site.server_ctx(s)))
    # 모니터링 대상
    mon = BUILD / "monitoring"
    oob = {r["device"]: r["address"].split("/")[0] for r in site.ips if r["purpose"] == "oob"}
    sw_t = [dict(targets=[oob[s["name"]]], labels=dict(device=s["name"], role=s["role"], rack=s["rack"]))
            for s in y["switches"] + [dict(f, role="firewall") for f in y["firewalls"]]]
    write(mon / "targets-network.json", json.dumps(sw_t, indent=2, ensure_ascii=False))
    nodes = {r["device"]: r["address"].split("/")[0] for r in site.ips if r["purpose"] in ("K8S-NODE",)}
    ceph = {r["device"]: r["address"].split("/")[0] for r in site.ips if r["purpose"] == "CEPH-PUBLIC" and r["device"].startswith("ceph")}
    srv = [dict(targets=[f"{nodes.get(s['name']) or ceph[s['name']]}:9100"],
                labels=dict(node=s["name"], role=s["role"], rack=s["rack"])) for s in y["servers"]]
    write(mon / "targets-node.json", json.dumps(srv, indent=2, ensure_ascii=False))
    bmc = [dict(targets=[host(y["prefixes"]["oob_bmc"], s["host"])], labels=dict(node=s["name"]))
           for s in y["servers"]]
    write(mon / "targets-bmc.json", json.dumps(bmc, indent=2, ensure_ascii=False))
    # 기대 BGP 세션 수 (알람 기준값)
    exp = {l["name"]: len(site.spines) * y["uplinks_per_spine"] for l in site.leaves}
    exp.update({s["name"]: len(site.leaves) * y["uplinks_per_spine"] for s in site.spines})
    write(mon / "expected-bgp.json", json.dumps(exp, indent=2))
    # 기대 세션 수를 Prometheus 기록 규칙으로: 실제 Established 수와 비교하는 알람이 이것을 쓴다
    rules = [{"record": "fabric_bgp_expected", "expr": f"vector({n})", "labels": {"device": d}}
             for d, n in exp.items()]
    write(mon / "expected-bgp.rules.yml", yaml.safe_dump({"groups": [{"name": "fabric-expected", "rules": rules}]},
                                                          allow_unicode=True, sort_keys=False))
    # 경로 프로브: 랙마다 워커 하나가 다른 랙의 Ceph로 스토리지 연결(VLAN 120)을 통해 ping
    # 흐름 2와 같은 길(리프 → 스파인 → 리프)을 MTU 9000으로 지나간다. 관리망에서 쏘면
    # 방화벽 구간(1500)을 지나 점보가 항상 실패하므로, 프로버는 워커 위에서 돈다
    ceph_pub, prod = site.vlans[120]["prefix"], site.vlans[110]["prefix"]
    probe = []
    for r in y["racks"]:
        w = next((s for s in y["servers"] if s["rack"] == r["name"] and s["role"] == "worker"), None)
        if not w:
            continue
        for c in y["servers"]:
            if c["role"] == "ceph" and c["rack"] != r["name"]:
                probe.append(dict(targets=[host(ceph_pub, c["host"])],
                                  labels=dict(prober=f"{host(prod, w['host'])}:9115", src=w["name"], dst=c["name"])))
    write(mon / "targets-path-probe.json", json.dumps(probe, indent=2, ensure_ascii=False))
    # Ansible 인벤토리
    inv = {"all": {"children": {
        "spine": {"hosts": {s["name"]: {"ansible_host": oob[s["name"]], "bgp_expected": exp[s["name"]]} for s in site.spines}},
        "leaf": {"hosts": {l["name"]: {"ansible_host": oob[l["name"]], "bgp_expected": exp[l["name"]],
                                       "vtep": site.lo(l)[1], "border": l.get("border", False)}
                           for l in site.leaves}},
    }}}
    write(BUILD / "ansible" / "hosts.yml", yaml.safe_dump(inv, allow_unicode=True, sort_keys=False))
    # 같은 인벤토리를 랩 컨테이너에 연결 (docker exec). 플레이북은 그대로 쓴다
    for grp in inv["all"]["children"].values():
        for n, v in grp["hosts"].items():
            v.update(ansible_host=f"clab-elice-{n}", ansible_connection="community.docker.docker")
    write(LAB / "generated" / "ansible-hosts.yml", yaml.safe_dump(inv, allow_unicode=True, sort_keys=False))
    write(BUILD / "report.md", site.report())
    build_lab(site, e)
    build_fw(site)


def build_fw(site):
    """방화벽 정책 원장 → 사람이 읽는 표 + 랩용 nftables"""
    pol = yaml.safe_load((ROOT / "inventory" / "fw-policy.yml").read_text(encoding="utf-8"))
    md = ["| ID | 출발 | 도착 | 대상 | 프로토콜/포트 | 동작 | 설명 |", "|---|---|---|---|---|---|---|"]
    for r in pol["rules"]:
        to = r["to"] if isinstance(r["to"], str) else ", ".join(r["to"])
        pp = r.get("proto", "any") + (" " + ",".join(map(str, r["ports"])) if r.get("ports") else "")
        md.append(f"| {r['id']} | {r['from']} | {to} | {r.get('dst', 'any')} | {pp} | {r['action']}{' +log' if r.get('log') else ''} | {r['desc']} |")
    md.append(f"| - | any | any | any | any | {pol['default']} | 기본 정책 |")
    write(BUILD / "fw-policy.md", "\n".join(md) + "\n")
    # 랩: 존 = 인터페이스
    zif = {"outside": "eth3", "prod": "fw0.901", "storage": "fw0.902", "mgmt": "eth4"}
    L = ["#!/usr/sbin/nft -f", "# inventory/fw-policy.yml 에서 생성. 손으로 고치지 말 것", "flush ruleset", "",
         "table inet fw {"]
    for k, v in pol["objects"].items():
        L.append(f"  # {k} = {v}")
    L += ["  chain forward {", "    type filter hook forward priority 0; policy drop;",
          "    ct state established,related accept", "    ct state invalid drop"]
    for r in pol["rules"]:
        tos = [r["to"]] if isinstance(r["to"], str) else r["to"]
        for to in tos:
            m = f'iifname "{zif[r["from"]]}" oifname "{zif[to]}"'
            if r.get("dst"):
                m += f" ip daddr {pol['objects'][r['dst']]}"
            if r.get("proto") == "icmp":
                m += " meta l4proto icmp"
            elif r.get("proto"):
                m += f" {r['proto']} dport {{ {', '.join(map(str, r['ports']))} }}"
            act = r["action"]
            if r.get("log"):
                L.append(f'    {m} log prefix "fw-r{r["id"]} " {act}')
            else:
                L.append(f'    {m} {act}  # r{r["id"]} {r["desc"]}')
    L += ['    log prefix "fw-default " drop', "  }", "}", "", "table ip nat {",
          "  chain prerouting {", "    type nat hook prerouting priority -100;"]
    for d in pol["nat"]["dnat"]:
        L.append(f'    iifname "{zif["outside"]}" ip daddr {d["public"]} {d["proto"]} dport {{ {", ".join(map(str, d["ports"]))} }} dnat to {d["to"]}')
    L += ["  }", "  chain postrouting {", "    type nat hook postrouting priority 100;"]
    for s in pol["nat"]["snat"]:
        L.append(f'    iifname "{zif[s["from"]]}" oifname "{zif[s["to"]]}" masquerade')
    L += ["  }", "}"]
    write(LAB / "generated" / "fw1" / "rules.nft", "\n".join(L) + "\n")


def build_lab(site, e):
    """운영 설계를 그대로 containerlab에 올린다. 포트 EthernetN → ethN, 서버 ens{b}f{i} → eth{2(b-1)+i+1}"""
    y = site.y

    def pm(p):
        if p.startswith("Ethernet"):
            return "eth" + p[8:]
        if p.startswith("ens"):
            b, i = int(p[3]), int(p[5])
            return f"eth{2 * (b - 1) + i + 1}"
        if p.startswith("port"):
            return "eth" + p[4:]
        return {"wan1": "eth3", "mgmt-zone": "eth4"}.get(p, p)

    g = LAB / "generated"
    for l in site.leaves:
        c = site.leaf_ctx(l, pm)
        write(g / l["name"] / "frr.conf", e.get_template("leaf-frr.j2").render(**c))
        write(g / l["name"] / "interfaces.sh", e.get_template("leaf-if.sh.j2").render(**c))
    for s in site.spines:
        write(g / s["name"] / "frr.conf", e.get_template("spine-frr.j2").render(**site.spine_ctx(s, pm)))
    vip_speakers = set(y["services"]["ingress_nodes"])   # Ingress 파드가 떠 있는 노드
    for s in y["servers"]:
        c = site.server_ctx(s)
        for b in c["bonds"]:
            b["nics"] = [pm(n) for n in b["nics"]]
        c["vip"] = y["services"]["web_vip"] if s["name"] in vip_speakers else ""
        c["k8s_asn"] = y["site"]["asn_base"]["k8s"]
        write(g / s["name"] / "setup.sh", e.get_template("lab-server.sh.j2").render(**c))
        if s["role"] == "worker":
            write(g / s["name"] / "frr.conf", e.get_template("lab-worker-frr.j2").render(**c))
    # 토폴로지: fw2, OOB, ISP는 생략. 방화벽 바깥에는 인터넷 사용자 하나, 관리망에는 배스천 하나
    keep = {d["name"] for d in y["switches"] if d["role"] in ("spine", "leaf")} | \
        {s["name"] for s in y["servers"]} | {"fw1"}
    links = [k for k in site.links if k["a"] in keep and k["b"] in keep and k["kind"] != "fw-ha"]
    nodes = {}
    for d in site.spines + site.leaves:
        nodes[d["name"]] = d["role"]
    for s in y["servers"]:
        nodes[s["name"]] = "worker" if s["role"] == "worker" else "host"
    topo = e.get_template("lab-topo.j2").render(
        nodes=nodes, links=[dict(a=k["a"], ap=pm(k["a_port"]), b=k["b"], bp=pm(k["b_port"])) for k in links])
    write(LAB / "elice.clab.yml", topo)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    site = Site(ROOT / "inventory" / "site.yml")
    errs = site.validate()
    for x in errs:
        print("  [X]", x)
    print(f"점검: 오류 {len(errs)}건, 장비 {len(site.y['switches']) + len(site.y['firewalls'])}대, "
          f"서버 {len(site.y['servers'])}대, 케이블 {len(site.links)}개, IP {len(site.ips)}개")
    if errs:
        sys.exit(1)
    if cmd == "build":
        build(site)
        print("생성 완료 → build/, lab/")


if __name__ == "__main__":
    main()
