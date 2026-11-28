"""Выкладка в Yandex Cloud: infra/gateway.yaml, .github/workflows/deploy-yc.yml, infra/setup.sh; секретов в коде нет."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GATEWAY = ROOT / "infra" / "gateway.yaml"
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-yc.yml"
SUBST = {"BUCKET", "ASSISTANT_FUNCTION_ID", "AUTH_FUNCTION_ID", "ISS_PROXY_FUNCTION_ID", "SA_ID"}


def _placeholders(text: str) -> set:
    return set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", text))


def _no_comments(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def test_gateway_placeholders_exact():
    text = GATEWAY.read_text(encoding="utf-8")
    assert _placeholders(_no_comments(text)) == SUBST
    # $latest — тег версии функции, envsubst со списком переменных его не трогает
    assert '"$latest"' in text


def test_gateway_openapi_structure():
    yaml = pytest.importorskip("yaml", reason="pyyaml не установлен")
    text = GATEWAY.read_text(encoding="utf-8")
    for v in SUBST:   # как после envsubst
        text = text.replace("${%s}" % v, f"test-{v.lower()}")
    spec = yaml.safe_load(text)
    assert str(spec["openapi"]).startswith("3.")
    scheme = spec["components"]["securitySchemes"]["siteAuth"]
    authz = scheme["x-yc-apigateway-authorizer"]
    assert authz["type"] == "function" and authz["function_id"] == "test-auth_function_id"
    paths = spec["paths"]
    post = paths["/api/assistant"]["post"]
    assert post["security"] == [{"siteAuth": []}]
    integ = post["x-yc-apigateway-integration"]
    assert integ["type"] == "cloud_functions" and integ["function_id"] == "test-assistant_function_id"
    # всё, кроме страницы входа «/», закрыто авторизатором
    for path, ops in paths.items():
        for method, op in ops.items():
            if path == "/":
                assert "security" not in op
                assert op["x-yc-apigateway-integration"]["function_id"] == "test-auth_function_id"
            else:
                assert op.get("security") == [{"siteAuth": []}], f"{method.upper()} {path} без авторизатора"
            if op["x-yc-apigateway-integration"]["type"] == "object_storage":
                assert op["x-yc-apigateway-integration"]["bucket"] == "test-bucket"


def test_gateway_assistant_route_text():
    """То же без pyyaml: маршрут POST /api/assistant с авторизатором и функцией assistant."""
    text = GATEWAY.read_text(encoding="utf-8")
    block = re.search(r"^  /api/assistant:\n((?:    .*\n|\n)+)", text, re.M)
    assert block, "нет маршрута /api/assistant"
    b = block.group(1)
    assert re.search(r"^    post:", b, re.M)
    assert "siteAuth" in b and "${ASSISTANT_FUNCTION_ID}" in b


def _steps(text: str) -> list[str]:
    """Тексты шагов job deploy (разбиение по «      - »)."""
    body = text.split("    steps:\n", 1)[1]
    return re.split(r"\n(?=      - )", body)


def test_workflow_skips_without_secret():
    text = WORKFLOW.read_text(encoding="utf-8")
    steps = _steps(text)
    first = steps[0]
    assert "id: cfg" in first
    assert "secrets.YC_SA_JSON_CREDENTIALS" in first
    m = re.search(r'if \[ "\$HAS_KEY" != "true" \]; then\n(.*?)\n\s*fi', first, re.S)
    assert m, "нет проверки секрета"
    branch = m.group(1)
    assert "::warning::" in branch and "enabled=false" in branch and "exit 0" in branch
    for s in steps[1:]:
        assert "if: steps.cfg.outputs.enabled == 'true'" in s, f"шаг без условия:\n{s[:200]}"
    # без секрета в первом шаге секреты не используются, кроме проверки наличия
    assert "secrets.YC_SA_JSON_CREDENTIALS != ''" in first


def test_workflow_cfg_step_runs_without_secret(tmp_path):
    """Скрипт первого шага в bash: пустой секрет -> предупреждение, enabled=false, код 0."""
    if not shutil.which("bash"):
        pytest.skip("нет bash")
    first = _steps(WORKFLOW.read_text(encoding="utf-8"))[0]
    script = re.search(r"run: \|\n((?:          .*\n?)+)", first).group(1)
    script = "\n".join(line[10:] for line in script.splitlines())
    out_file = tmp_path / "out"
    env = {"PATH": "/usr/bin:/bin", "HAS_KEY": "false", "GITHUB_OUTPUT": str(out_file)}
    r = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert "::warning::" in r.stdout
    assert out_file.read_text().strip() == "enabled=false"
    # секрет есть, переменных нет -> ошибка с перечнем
    env["HAS_KEY"] = "true"
    out_file.write_text("")
    r = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 1 and "YC_BUCKET" in r.stdout
    env.update(YC_BUCKET="b", YC_GATEWAY_ID="g", YC_SA_ID="s", YC_LOCKBOX_SECRET_ID="l")
    r = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and out_file.read_text().strip() == "enabled=true"


def test_workflow_envsubst_same_variables():
    text = WORKFLOW.read_text(encoding="utf-8")
    m = re.search(r"envsubst '([^']*)' < infra/gateway.yaml", text)
    assert m, "envsubst без явного списка переменных затрёт $latest"
    assert _placeholders(m.group(1)) == SUBST
    exported = re.search(r"export (BUCKET=.*)", text).group(1)
    for v in SUBST:
        assert re.search(rf"\b{v}\b", exported), f"{v} не экспортирована перед envsubst"


def test_workflow_envsubst_real(tmp_path):
    if not shutil.which("envsubst"):
        pytest.skip("нет envsubst (gettext)")
    env = {"BUCKET": "bkt", "ASSISTANT_FUNCTION_ID": "fa", "AUTH_FUNCTION_ID": "fb", "ISS_PROXY_FUNCTION_ID": "fi", "SA_ID": "sa"}
    r = subprocess.run(["envsubst", " ".join("${%s}" % v for v in sorted(SUBST))],
                       input=GATEWAY.read_text(encoding="utf-8"), env=env, capture_output=True, text=True)
    assert r.returncode == 0
    assert not re.search(r"\$\{[A-Z_]*\}", r.stdout)
    assert '"$latest"' in r.stdout


def test_setup_sh_syntax():
    if not shutil.which("bash"):
        pytest.skip("нет bash")
    r = subprocess.run(["bash", "-n", str(ROOT / "infra" / "setup.sh")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                    # AWS access key
    re.compile(r"\bYC[A-Za-z0-9_-]{38}\b"),                 # статический ключ Yandex Cloud
    re.compile(r"\bAQVN[A-Za-z0-9_-]{30,}"),                # API-ключ Yandex Cloud
    re.compile(r"\bt1\.[A-Za-z0-9_-]{40,}"),                # IAM-токен
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),            # токен GitHub
    re.compile(r"\b[0-9a-f]{64}\b"),                        # sha256 (например, хэш пароля в SITE_USERS)
    re.compile(r"(?i)(auth_key|password|secret|token|api_key)\s*[:=]\s*[\"'][A-Za-z0-9+/=_-]{16,}[\"']"),
    re.compile(r"(?i)Basic [A-Za-z0-9+/]{20,}={0,2}"),      # готовый ключ доступа / логин:пароль в base64
]
# закреплённые отпечатки публичных сертификатов Минцифры — не секрет
ALLOWED = {"D26D2D0231B7C39F92CC738512BA54103519E4405D68B5BD703E9788CA8ECF31",
           "BBBDE2103E790B999EC62BD03CF625A5A2E7C316E10AFE6A490EEDEAD8B3FD9B"}


def _scan_files():
    for d in ("functions", "infra", "web"):
        for p in (ROOT / d).rglob("*"):
            if p.is_file() and "vendor" not in p.parts and "__pycache__" not in p.parts \
                    and p.suffix in {".py", ".js", ".mjs", ".html", ".css", ".md", ".yaml", ".yml", ".sh", ".txt", ".json", ""}:
                yield p
    yield WORKFLOW


def test_no_secrets_in_code():
    found = []
    for p in _scan_files():
        text = p.read_text(encoding="utf-8", errors="ignore")
        for pat in SECRET_PATTERNS:
            for m in pat.finditer(text):
                if m.group(0).upper() not in ALLOWED:
                    found.append(f"{p.relative_to(ROOT)}: {m.group(0)[:40]}")
    assert not found, "Похоже на секрет:\n" + "\n".join(found)


def test_no_cert_or_key_files_in_functions():
    bad = [p for p in (ROOT / "functions").rglob("*") if p.suffix in {".pem", ".key", ".crt", ".env"}]
    # единственное исключение — публичные сертификаты УЦ Минцифры для прокси ISS (отпечатки: tests/test_iss_proxy.py)
    allowed = ROOT / "functions" / "iss-proxy" / "russian_trusted_ca.pem"
    assert [p for p in bad if p != allowed] == [], bad
    assert b"PRIVATE KEY" not in allowed.read_bytes()


def test_gitignore_keys_and_cert():
    lines = {x.strip() for x in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()}
    for pat in ("yc-sa-key.json", "*.sa-key.json", "functions/assistant/*.pem"):
        assert pat in lines, f".gitignore: нет {pat}"


def test_setup_sh_key_outside_repo_and_payload_not_in_args():
    text = (ROOT / "infra" / "setup.sh").read_text(encoding="utf-8")
    assert '--payload "$(' not in text and "--payload '$(" not in text, "секрет Lockbox аргументом виден в ps"
    for m in re.finditer(r"--output\s+(\S+)", text):
        assert m.group(1) == '"$KEY_FILE"', m.group(0)
    key_file = re.search(r'^KEY_FILE="([^"]+)"', text, re.M).group(1)
    key_dir = re.search(r'^KEY_DIR="([^"]+)"', text, re.M).group(1)
    assert key_file.startswith("$KEY_DIR/") and key_dir.startswith("$HOME/"), (key_dir, key_file)


def test_readme_no_password_hash_example():
    text = (ROOT / "infra" / "README.md").read_text(encoding="utf-8")
    assert "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8" not in text.lower()
    assert "pbkdf2_hmac('sha256'" in text and "200000" in text


def test_setup_sh_site_users_accepted_by_auth(monkeypatch):
    """Строку SITE_USERS из infra/setup.sh принимает функция auth."""
    import importlib.util
    import sys
    text = (ROOT / "infra" / "setup.sh").read_text(encoding="utf-8")
    code = re.search(r"SITE_USERS=\$\(printf '%s' \"\$pw\" \| python3 -c '([^']+)' \"\$login\"\)", text).group(1)
    r = subprocess.run([sys.executable, "-I", "-c", code, "ivan"], input=b"dlinnyj-parol-16+", capture_output=True)
    assert r.returncode == 0, r.stderr
    spec = importlib.util.spec_from_file_location("auth_index_infra", ROOT / "functions" / "auth" / "index.py")
    auth = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(auth)
    import base64
    monkeypatch.setenv("SITE_USERS", r.stdout.decode().strip())
    hdr = "Basic " + base64.b64encode("ivan:dlinnyj-parol-16+".encode()).decode()
    assert auth.check_basic(hdr) == "ivan"


def test_deploy_assistant_uses_yandexgpt_env_no_gigachat():
    wf = WORKFLOW.read_text(encoding="utf-8")
    assert not re.search(r"(?i)gigachat|минцифр|russian_trusted|ca_bundle", wf)
    m = re.search(r"^\s*deploy assistant \\\n(.*)$", wf, re.M)
    assert m, "нет вызова deploy assistant"
    assert "YC_FOLDER_ID=${YC_FOLDER_ID}" in m.group(1) and "YANDEX_MODEL=" in m.group(1)
    assert "--secret" not in m.group(1), "у assistant больше нет секретов Lockbox"
    assert "--execution-timeout 30s" in wf


def test_setup_sh_yandexgpt_role_no_gigachat_key():
    text = (ROOT / "infra" / "setup.sh").read_text(encoding="utf-8")
    assert not re.search(r"(?i)gigachat", text)
    assert re.search(r'^folder ai\.languageModels\.user "\$RUNTIME_ID"', text, re.M)
    readme = (ROOT / "infra" / "README.md").read_text(encoding="utf-8")
    assert "ai.languageModels.user" in readme and "ia-runtime" in readme


# ---------------------------------------------------------------- прокси ISS и ЦБ (CONTRACT A)
def _gateway_spec():
    yaml = pytest.importorskip("yaml", reason="pyyaml не установлен")
    text = GATEWAY.read_text(encoding="utf-8")
    for v in SUBST:
        text = text.replace("${%s}" % v, f"test-{v.lower()}")
    return yaml.safe_load(text)


def test_gateway_substitutions_all_listed_in_workflow():
    """Каждая переменная из gateway.yaml есть в списке envsubst и в export; лишних в списке нет."""
    used = _placeholders(_no_comments(GATEWAY.read_text(encoding="utf-8")))
    wf = WORKFLOW.read_text(encoding="utf-8")
    listed = _placeholders(re.search(r"envsubst '([^']*)' < infra/gateway.yaml", wf).group(1))
    assert used == listed and "ISS_PROXY_FUNCTION_ID" in listed
    exported = re.search(r"export (BUCKET=.*)", wf).group(1)
    assert all(re.search(rf"\b{v}\b", exported) for v in used)


def test_gateway_iss_and_cbr_routes_before_catch_all_with_auth():
    spec = _gateway_spec()
    order = list(spec["paths"])
    assert "/iss/{path+}" in order and "/cbr/{path+}" in order and "/{path+}" in order
    assert order.index("/iss/{path+}") < order.index("/{path+}") and order.index("/cbr/{path+}") < order.index("/{path+}")
    for route in ("/iss/{path+}", "/cbr/{path+}"):
        assert list(spec["paths"][route]) == ["get"], "прокси — только GET"
        op = spec["paths"][route]["get"]
        assert op["security"] == [{"siteAuth": []}]
        integ = op["x-yc-apigateway-integration"]
        assert integ["type"] == "cloud_functions" and integ["function_id"] == "test-iss_proxy_function_id"
        assert integ["tag"] == "$latest" and integ["service_account_id"] == "test-sa_id"
        p = op["parameters"][0]
        assert p["name"] == "path" and p["in"] == "path" and p["required"] is True


def test_gateway_proxy_routes_text_order():
    """То же без pyyaml: порядок в файле."""
    text = GATEWAY.read_text(encoding="utf-8")
    pos = [text.index(f"\n  {r}:\n") for r in ("/iss/{path+}", "/cbr/{path+}", "/{path+}")]
    assert pos == sorted(pos)
    for route, nxt in (("/iss/{path+}", "/cbr/{path+}"), ("/cbr/{path+}", "/{path+}")):
        block = text[text.index(f"\n  {route}:\n"):text.index(f"\n  {nxt}:\n")]
        assert re.search(r"^    get:", block, re.M) and not re.search(r"^    (post|put|delete|patch):", block, re.M)
        assert "- siteAuth: []" in block and "${ISS_PROXY_FUNCTION_ID}" in block and "type: cloud_functions" in block


def test_workflow_deploys_iss_proxy_without_secrets():
    wf = WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"^\s*deploy iss-proxy\s*$", wf, re.M), "iss-proxy выкладывается без секретов и доп. флагов"
    assert "ISS_PROXY_FUNCTION_ID=$(fid iss-proxy)" in wf
    assert 'echo "ISS_PROXY_FUNCTION_ID=$ISS_PROXY_FUNCTION_ID" >> "$GITHUB_ENV"' in wf
    # общие параметры версии (deploy): 256 МБ, 30 с, сервисный аккаунт ia-runtime (YC_SA_ID)
    assert "--memory 256m" in wf and "--execution-timeout 30s" in wf
    # архив функции — каталог целиком, значит russian_trusted_ca.pem уезжает вместе с index.py
    assert 'zip -qr "$zip_path" .' in wf
