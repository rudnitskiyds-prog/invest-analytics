#!/usr/bin/env bash
# Первичная настройка Yandex Cloud для FinServ. Повторный запуск безопасен: существующее не пересоздаётся.
# Нужно: установленный и настроенный `yc` (yc init, выбран нужный каталог), python3.
# Настройки — переменными окружения (все необязательны, кроме имени бакета, — его спросим):
#   YC_BUCKET           имя бакета (уникально во всём Object Storage, латиница, цифры, дефис)
#   DEPLOY_SA_NAME      аккаунт выкладки из GitHub (по умолчанию ia-deploy)
#   RUNTIME_SA_NAME     аккаунт функций и шлюза (по умолчанию ia-runtime)
#   SECRET_NAME         секрет Lockbox (по умолчанию invest-analytics)
#   GATEWAY_NAME        API Gateway (по умолчанию invest-analytics)
#   GIGACHAT_AUTH_KEY   ключ авторизации GigaChat — если не задан, спросим (ввод скрыт)
#   SITE_USERS          готовая строка логин:соль:хэш,... — если не задана, спросим логин и пароль (ввод скрыт)
# Ключи и пароли аргументами командной строки не принимаются и в аргументы процессов не попадают.
set -euo pipefail

DEPLOY_SA_NAME=${DEPLOY_SA_NAME:-ia-deploy}
RUNTIME_SA_NAME=${RUNTIME_SA_NAME:-ia-runtime}
SECRET_NAME=${SECRET_NAME:-invest-analytics}
GATEWAY_NAME=${GATEWAY_NAME:-invest-analytics}
KEY_DIR="$HOME/.yc-invest-analytics"
KEY_FILE="$KEY_DIR/sa-key.json"

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
ok()   { printf '    ✓ %s\n' "$*"; }
warn() { printf '    \033[33m! %s\033[0m\n' "$*"; }
die()  { printf '\n\033[31mОшибка: %s\033[0m\n' "$*" >&2; exit 1; }
ask_yes() { local a; read -r -p "    $1 [y/N] " a; [[ $a == [yYдД]* ]]; }
json_id() { python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])'; }

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
chmod 700 "$TMP"

command -v yc >/dev/null || die "не найден yc. Установите: https://yandex.cloud/ru/docs/cli/quickstart"
command -v python3 >/dev/null || die "не найден python3"

FOLDER_ID=$(yc config get folder-id 2>/dev/null || true)
CLOUD_ID=$(yc config get cloud-id 2>/dev/null || true)
[ -n "$FOLDER_ID" ] || die "в yc не выбран каталог. Выполните: yc init"
say "Каталог $FOLDER_ID (облако $CLOUD_ID)"

if [ -z "${YC_BUCKET:-}" ]; then
  read -r -p "    Имя бакета (например, invest-analytics-ivanov): " YC_BUCKET
fi
[[ $YC_BUCKET =~ ^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$ ]] || die "недопустимое имя бакета: $YC_BUCKET"

# --- сервисные аккаунты --------------------------------------------------------------------
sa() {   # $1 — имя, $2 — описание; печатает id
  local id
  id=$(yc iam service-account get --name "$1" --format json 2>/dev/null | json_id || true)
  if [ -z "$id" ]; then
    id=$(yc iam service-account create --name "$1" --description "$2" --format json | json_id)
    ok "$1 создан: $id" >&2
  else
    ok "$1 уже есть: $id" >&2
  fi
  printf '%s' "$id"
}
say "Сервисные аккаунты"
DEPLOY_ID=$(sa "$DEPLOY_SA_NAME" "FinServ: выкладка из GitHub Actions")
RUNTIME_ID=$(sa "$RUNTIME_SA_NAME" "FinServ: функции и API Gateway")

# --- бакет ---------------------------------------------------------------------------------
say "Бакет $YC_BUCKET (закрытый)"
if yc storage bucket get --name "$YC_BUCKET" >/dev/null 2>&1; then
  ok "уже есть"
else
  # без --public-read/--public-list: доступ только по ролям и ACL
  yc storage bucket create --name "$YC_BUCKET" --default-storage-class standard --max-size 1073741824 >/dev/null \
    || die "не удалось создать бакет (имя занято? выберите другое через YC_BUCKET=...)"
  ok "создан (лимит 1 ГБ)"
fi

# --- секрет Lockbox ------------------------------------------------------------------------
read_secrets() {
  if [ -z "${GIGACHAT_AUTH_KEY:-}" ]; then
    read -r -s -p "    Ключ авторизации GigaChat (Authorization key, ввод скрыт): " GIGACHAT_AUTH_KEY; echo
  fi
  [ -n "$GIGACHAT_AUTH_KEY" ] || die "пустой ключ GigaChat"
  if [ -z "${SITE_USERS:-}" ]; then
    local login pw pw2
    read -r -p "    Логин для входа на сайт: " login
    echo "    Пароль — случайный, не короче 16 символов (например, из менеджера паролей)."
    read -r -s -p "    Пароль (ввод скрыт): " pw; echo
    read -r -s -p "    Пароль ещё раз: " pw2; echo
    [ -n "$login" ] || die "пустой логин"
    [ "$pw" = "$pw2" ] || die "пароли не совпадают"
    [ "${#pw}" -ge 16 ] || die "пароль короче 16 символов"
    [[ $login != *[:,]* ]] || die "логин не должен содержать «:» и «,»"
    # пароль идёт в python через stdin, не аргументом
    SITE_USERS=$(printf '%s' "$pw" | python3 -c 'import hashlib,os,sys;s=os.urandom(16);print(sys.argv[1]+":"+s.hex()+":"+hashlib.pbkdf2_hmac("sha256",sys.stdin.buffer.read(),s,200000).hex())' "$login")
    unset pw pw2
    echo "    Других пользователей можно добавить позже новой версией секрета, через запятую (см. README)."
  fi
}

write_payload() {   # JSON во временный файл (права 600); значения передаём через окружение python
  ( umask 077
    GK="$GIGACHAT_AUTH_KEY" SU="$SITE_USERS" python3 -c 'import json,os;print(json.dumps([{"key":"GIGACHAT_AUTH_KEY","text_value":os.environ["GK"]},{"key":"SITE_USERS","text_value":os.environ["SU"]}]))' > "$TMP/payload.json" )
}

manual_secret() {
  rm -f "$TMP/payload.json"
  die "yc не принял секрет из файла. Создайте секрет вручную в консоли (README, шаг 6: Lockbox → «Создать секрет»,
       имя $SECRET_NAME, ключи GIGACHAT_AUTH_KEY и SITE_USERS) и запустите скрипт ещё раз — он подхватит секрет."
}

say "Секрет Lockbox $SECRET_NAME"
SECRET_ID=$(yc lockbox secret get --name "$SECRET_NAME" --format json 2>/dev/null | json_id || true)
if [ -z "$SECRET_ID" ]; then
  read_secrets; write_payload
  # ponytail: чтение --payload из stdin («-») не проверено на живом yc; при отказе — ручной путь через консоль.
  SECRET_ID=$(yc lockbox secret create --name "$SECRET_NAME" --payload - --format json < "$TMP/payload.json" | json_id) \
    || manual_secret
  ok "создан: $SECRET_ID"
elif ask_yes "Секрет уже есть ($SECRET_ID). Заменить ключ GigaChat и пользователей новой версией?"; then
  read_secrets; write_payload
  yc lockbox secret add-version --id "$SECRET_ID" --payload - < "$TMP/payload.json" >/dev/null || manual_secret
  ok "добавлена новая версия (функции подхватят её при следующей выкладке)"
else
  ok "оставлен как есть"
fi
rm -f "$TMP/payload.json"
unset GIGACHAT_AUTH_KEY SITE_USERS

# --- API Gateway ---------------------------------------------------------------------------
say "API Gateway $GATEWAY_NAME"
GW_JSON=$(yc serverless api-gateway get --name "$GATEWAY_NAME" --format json 2>/dev/null || true)
if [ -z "$GW_JSON" ]; then
  # заглушка: настоящую спецификацию (infra/gateway.yaml) ставит workflow выкладки
  cat > "$TMP/stub.yaml" <<'YAML'
openapi: 3.0.0
info: {title: invest-analytics, version: 0.0.1}
paths:
  /:
    get:
      x-yc-apigateway-integration:
        type: dummy
        http_code: 200
        http_headers: {Content-Type: "text/plain; charset=utf-8"}
        content: {"*": "Сайт ещё не выложен: запустите workflow «Выкладка в Yandex Cloud»."}
YAML
  GW_JSON=$(yc serverless api-gateway create --name "$GATEWAY_NAME" --spec "$TMP/stub.yaml" --format json)
  ok "создан"
else
  ok "уже есть"
fi
read -r GATEWAY_ID GATEWAY_DOMAIN < <(printf '%s' "$GW_JSON" | python3 -c 'import json,sys;g=json.load(sys.stdin);print(g["id"],g["domain"])')

# --- роли ----------------------------------------------------------------------------------
has_binding() {   # $1 — команда ресурса ("iam service-account"), $2 — id ресурса, $3 — роль, $4 — id аккаунта
  # shellcheck disable=SC2086
  yc $1 list-access-bindings --id "$2" --format json 2>/dev/null | python3 -c '
import json,sys
d=sys.stdin.read().strip(); r,s=sys.argv[1:]
sys.exit(0 if any(b["role_id"]==r and b["subject"]["id"]==s for b in (json.loads(d) if d else [])) else 1)' "$3" "$4"
}
grant() {   # как has_binding; $5 — подпись. Возвращает 1, если назначить не удалось.
  if has_binding "$1" "$2" "$3" "$4"; then ok "$5: $3 — уже есть"; return 0; fi
  # shellcheck disable=SC2086
  if yc $1 add-access-binding --id "$2" --role "$3" --subject "serviceAccount:$4" >/dev/null; then
    ok "$5: $3 — назначена"
  else
    return 1
  fi
}
folder() { grant "resource-manager folder" "$FOLDER_ID" "$1" "$2" "$3 (каталог)" || die "не удалось назначить $1"; }

say "Роли $DEPLOY_SA_NAME (выкладка)"
folder functions.editor "$DEPLOY_ID" "$DEPLOY_SA_NAME"            # функции создаёт workflow — уровень ресурса недоступен
grant "serverless api-gateway" "$GATEWAY_ID" api-gateway.editor "$DEPLOY_ID" "$DEPLOY_SA_NAME (шлюз)" \
  || { warn "на шлюз назначить не удалось — назначаю на каталог"; folder api-gateway.editor "$DEPLOY_ID" "$DEPLOY_SA_NAME"; }
grant "iam service-account" "$RUNTIME_ID" iam.serviceAccounts.user "$DEPLOY_ID" "$DEPLOY_SA_NAME (на $RUNTIME_SA_NAME)" \
  || die "не удалось назначить iam.serviceAccounts.user"
grant "iam service-account" "$DEPLOY_ID" iam.serviceAccounts.accessKeyAdmin "$DEPLOY_ID" "$DEPLOY_SA_NAME (на себя)" \
  || die "не удалось назначить iam.serviceAccounts.accessKeyAdmin"
# Нужна ли выкладке эта роль для `function version create --secret`, точно не известно; на один секрет — безопасно.
grant "lockbox secret" "$SECRET_ID" lockbox.payloadViewer "$DEPLOY_ID" "$DEPLOY_SA_NAME (секрет)" \
  || die "не удалось назначить lockbox.payloadViewer"

say "Роли $RUNTIME_SA_NAME (функции и шлюз)"
grant "lockbox secret" "$SECRET_ID" lockbox.payloadViewer "$RUNTIME_ID" "$RUNTIME_SA_NAME (секрет)" \
  || die "не удалось назначить lockbox.payloadViewer"
folder functions.functionInvoker "$RUNTIME_ID" "$RUNTIME_SA_NAME"  # функции создаёт workflow — уровень ресурса недоступен

say "Доступ к бакету (ACL бакета вместо ролей на каталог)"
# --grants заменяет весь ACL: выкладка — полный доступ, рабочий аккаунт — только чтение.
if yc storage bucket update --name "$YC_BUCKET" \
     --grants "grant-type=grant-type-account,grantee-id=$DEPLOY_ID,permission=permission-full-control" \
     --grants "grant-type=grant-type-account,grantee-id=$RUNTIME_ID,permission=permission-read" >/dev/null; then
  ok "$DEPLOY_SA_NAME — полный доступ, $RUNTIME_SA_NAME — чтение"
else
  warn "ACL бакета задать не удалось — назначаю роли на каталог (в отдельном каталоге это почти то же самое)"
  folder storage.editor "$DEPLOY_ID" "$DEPLOY_SA_NAME"
  folder storage.viewer "$RUNTIME_ID" "$RUNTIME_SA_NAME"
fi

# --- ключ для GitHub -----------------------------------------------------------------------
say "Авторизованный ключ $DEPLOY_SA_NAME для GitHub"
if [ -f "$KEY_FILE" ]; then
  ok "файл $KEY_FILE уже есть — используйте его"
elif ask_yes "Создать новый ключ в $KEY_FILE? (нужен один раз; старые ключи можно удалить в консоли)"; then
  ( umask 077; mkdir -p "$KEY_DIR"; yc iam key create --service-account-id "$DEPLOY_ID" --output "$KEY_FILE" >/dev/null )
  ok "создан $KEY_FILE"
fi

cat <<EOF

Готово. Внесите в GitHub (Settings → Secrets and variables → Actions):

  Secrets (вкладка Secrets):
    YC_SA_JSON_CREDENTIALS = всё содержимое файла $KEY_FILE
                             (macOS: pbcopy < "$KEY_FILE"; затем удалите файл: rm "$KEY_FILE")
    YC_CLOUD_ID            = $CLOUD_ID
    YC_FOLDER_ID           = $FOLDER_ID

  Variables (вкладка Variables):
    YC_BUCKET              = $YC_BUCKET
    YC_GATEWAY_ID          = $GATEWAY_ID
    YC_SA_ID               = $RUNTIME_ID   ($RUNTIME_SA_NAME)
    YC_LOCKBOX_SECRET_ID   = $SECRET_ID

Затем запустите workflow «Выкладка в Yandex Cloud» (Actions → Run workflow).
Адрес сайта: https://$GATEWAY_DOMAIN/  (откройте корень — браузер спросит логин и пароль)
Не забудьте бюджет-уведомление в биллинге (infra/README.md, шаг 10).
EOF
