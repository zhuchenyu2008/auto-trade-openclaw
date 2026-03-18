from __future__ import annotations

import os
from pathlib import Path


def mirror_source_local_env(source_path: Path, source_config: dict, target_dir: Path) -> dict[str, str]:
    env_names = [
        str(source_config.get("web", {}).get("pin_plaintext_env", "") or "").strip(),
        str(source_config.get("telegram", {}).get("bot_token_env", "") or "").strip(),
        str(source_config.get("okx", {}).get("api_key_env", "") or "").strip(),
        str(source_config.get("okx", {}).get("api_secret_env", "") or "").strip(),
        str(source_config.get("okx", {}).get("passphrase_env", "") or "").strip(),
    ]
    inline_values = {
        str(source_config.get("telegram", {}).get("bot_token_env", "") or "").strip(): str(
            source_config.get("telegram", {}).get("bot_token", "") or ""
        ).strip(),
        str(source_config.get("okx", {}).get("api_key_env", "") or "").strip(): str(
            source_config.get("okx", {}).get("api_key", "") or ""
        ).strip(),
        str(source_config.get("okx", {}).get("api_secret_env", "") or "").strip(): str(
            source_config.get("okx", {}).get("api_secret", "") or ""
        ).strip(),
        str(source_config.get("okx", {}).get("passphrase_env", "") or "").strip(): str(
            source_config.get("okx", {}).get("passphrase", "") or ""
        ).strip(),
    }

    resolved: dict[str, str] = {}
    for env_path in _env_search_paths(source_path):
        for key, value in _read_env_file(env_path).items():
            if key in env_names and key not in resolved and value:
                resolved[key] = value

    for env_name in env_names:
        if not env_name:
            continue
        current = str(os.environ.get(env_name, "") or "").strip()
        if current:
            resolved[env_name] = current
            continue
        inline = inline_values.get(env_name, "")
        if inline and env_name not in resolved:
            resolved[env_name] = inline

    if resolved:
        _write_env_file(target_dir / ".env", resolved)
    return resolved


def _env_search_paths(source_path: Path) -> list[Path]:
    paths = [(source_path.parent / ".env").resolve()]
    project_env = (source_path.resolve().parents[1] / ".env").resolve()
    if project_env not in paths:
        paths.append(project_env)
    return paths


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            values[key] = value.strip().strip("'\"")
    return values


def _write_env_file(path: Path, values: dict[str, str]) -> None:
    path.write_text(
        "\n".join(f"{key}={value}" for key, value in sorted(values.items())) + "\n",
        encoding="utf-8",
    )
