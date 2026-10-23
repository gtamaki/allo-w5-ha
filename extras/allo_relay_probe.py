#!/usr/bin/env python3
"""Allo w5 relay-button probe.

Descobre se apertar "Abrir/Fechar" no app Intelbras Allo deixa algum rastro
visivel na nuvem Meari (o que permitiria ao HA acionar a porteira).

Uso (no Mac/PC, com a pasta do zip descompactada ao lado):
    pip3 install requests pycryptodome
    python3 allo_relay_probe.py --integration ./cloudplus --email CONTA_DO_HA@...

Depois que aparecer "MONITORANDO", abra o video do W5 no iPhone e aperte
"Abrir/Fechar" 2-3 vezes, com uns 10 s entre cada. Ctrl+C para sair.
"""
from __future__ import annotations

import argparse
import getpass
import importlib
import importlib.util
import json
import sys
import time
import types
from pathlib import Path


def load_api(integration_dir: Path):
    # Stub homeassistant so the integration package imports outside HA.
    for name in ("homeassistant", "homeassistant.core", "homeassistant.config_entries",
                 "homeassistant.const", "homeassistant.helpers",
                 "homeassistant.helpers.entity"):
        sys.modules.setdefault(name, types.ModuleType(name))
    spec = importlib.util.spec_from_file_location(
        "cp", integration_dir / "__init__.py",
        submodule_search_locations=[str(integration_dir)])
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["cp"] = pkg
    return importlib.import_module("cp.api")


def predict_path(dev: dict) -> str:
    """Mirror of the app's isIothub(): True -> cloud IoT, False -> direct P2P."""
    try:
        dev_type = int(dev.get("devTypeID", -1))
        proto = int(dev.get("protocolVersion", -1))
        ver = int(dev.get("ver", -1))
        spp = int(dev.get("spp", -1))
    except (TypeError, ValueError):
        return "desconhecido (campos ausentes)"
    if dev_type == 6:
        return "NUVEM (IoT)"
    if proto < 4:
        return "P2P direto (protocolVersion<4) - HA nao enxerga"
    if ver < 15:
        return "depende de outro flag (ver<15) - indefinido"
    if spp != -1 and (spp & 2) != 2:
        return "P2P direto (spp) - HA nao enxerga"
    return "NUVEM (IoT) - promissor"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--integration", type=Path, required=True,
                    help="pasta custom_components/cloudplus")
    ap.add_argument("--email", required=True)
    ap.add_argument("--country", default="BR")
    ap.add_argument("--phone-code", default="55")
    ap.add_argument("--interval", type=float, default=1.5)
    args = ap.parse_args()

    api_mod = load_api(args.integration.resolve())
    pw = getpass.getpass("Senha da conta: ")
    api = api_mod.MeariApiClient(args.email, pw, country_code=args.country,
                                 phone_code=args.phone_code, app_profile="intelbras")
    api.login()
    devs = api.get_camera_devices()
    if not devs:
        sys.exit("Nenhum dispositivo na conta (o W5 foi compartilhado?)")

    for i, d in enumerate(devs):
        keys = {k: d.get(k) for k in ("deviceName", "snNum", "deviceID", "devTypeID",
                                      "protocolVersion", "ver", "spp", "rel", "iotType")}
        print(f"[{i}] {json.dumps(keys, ensure_ascii=False)}")
        print(f"    caminho previsto do botao: {predict_path(d)}")
    dev = devs[0]
    sn = dev["snNum"]

    def snapshot() -> dict:
        snap = {}
        try:
            snap["cfg"] = api.get_device_iot_config(sn)
        except Exception as e:  # noqa: BLE001
            snap["cfg_err"] = str(e)
        try:
            snap["v824_194"] = api.get_device_iot_values(sn, ["824", "194"])
        except Exception as e:  # noqa: BLE001
            snap["v_err"] = str(e)
        try:
            evs = api.get_new_device_events() or []
            snap["events"] = [json.dumps(e, sort_keys=True, ensure_ascii=False)[:300]
                              for e in evs]
        except Exception as e:  # noqa: BLE001
            snap["ev_err"] = str(e)
        return snap

    prev = snapshot()
    print("valores iniciais 824/194:", prev.get("v824_194"))
    print("\nMONITORANDO - aperte Abrir/Fechar no app agora (Ctrl+C para sair)\n")
    try:
        while True:
            time.sleep(args.interval)
            cur = snapshot()
            ts = time.strftime("%H:%M:%S")
            old_cfg, new_cfg = prev.get("cfg") or {}, cur.get("cfg") or {}
            for k in sorted(set(old_cfg) | set(new_cfg), key=str):
                if old_cfg.get(k) != new_cfg.get(k):
                    print(f"{ts} IOT {k}: {old_cfg.get(k)!r} -> {new_cfg.get(k)!r}")
            if cur.get("v824_194") != prev.get("v824_194"):
                print(f"{ts} 824/194: {prev.get('v824_194')} -> {cur.get('v824_194')}")
            for e in cur.get("events", []):
                if e not in prev.get("events", []):
                    print(f"{ts} EVENTO {e}")
            prev = cur
    except KeyboardInterrupt:
        print("\nfim")


if __name__ == "__main__":
    main()
