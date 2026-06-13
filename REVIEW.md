# 🔍 Full Repo Review: rmichelena/mcp-server-dash

**Branch:** main · **Commit:** `b82aa2a` · **Scope:** Full repository (5 src files, ~2158 LOC, 5 test files)

**Reviewers:** GPT-5.5 · GLM-5.1 · DeepSeek V4 Pro

---

## Summary

Repo bien estructurado con separación limpia de responsabilidades (API client, auth, token store, renderer, MCP layer). Tests con mocking HTTP vía respx, keyring in-memory, y coverage sólida de happy/error paths.

Hay un par de issues del upstream (dropbox) que PR #11 ya corrige en la rama `fix/token-refresh-persistence` pero que aún no están en `main` del fork. Los marco como such.

**Verdict: 🟡 1 Critical, 4 High, 5 Medium, 1 Low**

---

## 🔴 CRITICAL

### 1. No HTTP timeout on token exchange — puede colgarse indefinidamente
**File:** `src/auth_pkce.py:110` · **DeepSeek V4 Pro**

`exchange_code_for_token` crea `httpx.AsyncClient()` sin timeout. Si el endpoint de Dropbox está lento o caído, el servidor MCP queda completamente bloqueado esperando.

**Fix:** `httpx.AsyncClient(timeout=httpx.Timeout(30.0))`

---

## 🔴 HIGH

### 2. auth_pkce.py no se incluye en el package instalado
**File:** `pyproject.toml:40` · **GPT-5.5**

`py-modules` incluye `mcp_server_dash`, `dash_api`, `renderer`, `token_store` pero **omite `auth_pkce`**. Un `pip install .` produce un binario que falla con `ModuleNotFoundError` al importar auth_pkce.

**Fix:** Añadir `"auth_pkce"` a `tool.setuptools.py-modules`, o usar package discovery.

### 3. Server mode expone el token de Dropbox a cualquier cliente MCP alcanzable
**File:** `src/mcp_server_dash.py:137` · **GPT-5.5**

En `--mode server`, un `token_store` global se comparte entre todos los clientes. Sin auth por sesión ni validación de cliente, cualquier que alcance el endpoint MCP puede buscar contenido Dash usando el token del usuario autenticado.

**Fix:** Mantener loopback-only por defecto. Documentar el threat model. Si se necesita remote, añadir auth MCP.

### 4. `token_store.load()` bloquea el startup con llamada síncrona a Dropbox
**File:** `src/mcp_server_dash.py:62` · **GLM-5.1**

`load()` se ejecuta a nivel de módulo (import time) y llama a `dbx.users_get_current_account()` — bloqueante, sin timeout. Si hay un token stale y Dropbox está lento o inalcanzable, el server entero colapsa en startup.

**Fix:** Mover `load()` a `main()` o lazy init. Añadir timeout al SDK: `dropbox.Dropbox(token, timeout=30)`.

### 5. Errores 4xx no-reintentables se reintentan hasta 3 veces
**File:** `src/dash_api.py:153` · **DeepSeek V4 Pro**

`_post()` captura `HTTPStatusError` (de `raise_for_status()`) con `except Exception` y reintenta. Un 400 Bad Request o 403 Forbidden se reintenta inútilmente, gastando tiempo y potencialmente causando side effects.

**Fix:** Antes de `raise_for_status()`, chequear el status code. Solo reintentar en errores transitorios (network, 429, 5xx).

---

## 🟡 MEDIUM

### 6. Refresh token solicitado pero nunca persistido
**File:** `src/auth_pkce.py:95` / `src/mcp_server_dash.py:215` · **GPT-5.5 · GLM-5.1**

El flow OAuth pide `token_access_type=offline` → Dropbox devuelve `refresh_token`, pero `dash_authenticate` solo extrae `access_token`. Al expirar (~4h), hay que re-autenticar manualmente.

> ℹ️ **PR #11 ya corrige esto** en la rama `fix/token-refresh-persistence`. Merge pendiente.

### 7. `load()` destruye tokens válidos en errores transitorios
**File:** `src/token_store.py:95-98` · **GPT-5.5 · DeepSeek V4 Pro**

El broad `except Exception` en `load()` llama `self.clear()` ante cualquier error — incluyendo timeouts de red, DNS caído, etc. Un blip transitorio borra credenciales permanentemente.

> ℹ️ **PR #11 ya corrige esto**. Merge pendiente.

### 8. Llamada síncrona al SDK de Dropbox dentro de handler async
**File:** `src/mcp_server_dash.py:189` · **GLM-5.1**

`dash_authenticate` es async pero `dbx.users_get_current_account()` es bloqueante. Mientras está en flight, todo el event loop se congela — otros clientes MCP, heartbeats SSE, todo encolado.

**Fix:** `await asyncio.to_thread(dbx.users_get_current_account)`

### 9. Dead code: RuntimeError inalcanzable en `_post`
**File:** `src/dash_api.py:164` · **DeepSeek V4 Pro**

Después del loop de retry, `last_exc` siempre es non-None, así que la línea 164 (`raise RuntimeError("Request failed with no response and no exception")` ) nunca se ejecuta.

**Fix:** Remover las líneas 164-165 o simplificar el control flow.

### 10. `logging.basicConfig` a nivel de módulo contamina el root logger
**File:** `src/mcp_server_dash.py:43` · **DeepSeek V4 Pro**

Se ejecuta al importar el módulo (antes del `__name__ == "__main__"`), reconfigurando el root logger para todo el proceso. Aplicaciones que importen este módulo (tests, MCP Inspector) pierden su config de logging.

**Fix:** Mover dentro de `main()` o usar `logging.getLogger(__name__)` con handler propio.

---

## 🟢 LOW

### 11. `global token_store` innecesario en `dash_authenticate`
**File:** `src/mcp_server_dash.py:209` · **DeepSeek V4 Pro · GLM-5.1**

La función solo llama métodos en `token_store`, nunca lo reasigna. El `global` es ruido.

**Fix:** Remover la línea.

---

## 📊 Cobertura por Reviewer

| Reviewer | Findings | Unique | Critical | High | Medium | Low |
|----------|----------|--------|----------|------|--------|-----|
| GPT-5.5 | 5 | 2 | 0 | 2 | 2 | 0 |
| GLM-5.1 | 7 | 2 | 0 | 2 | 1+ | 0 |
| DeepSeek V4 Pro | 7 | 4 | 1 | 2 | 2 | 1 |
| **Consolidado** | **11** | — | **1** | **4** | **5** | **1** |

---

## ✅ Fortalezas destacadas

- PKCE OAuth2 con lifecycle correcto del code_verifier (generado, usado una vez, limpiado)
- Backoff/retry con soporte de `Retry-After` header
- Dual token persistence (keyring + file fallback) con graceful degradation
- Validación de inputs (`file_type`, `max_results`) en los tools de MCP
- Decoradores `require_auth` y `require_app_key` como guardrails limpios
- `renderer.py` particularmente limpio y bien testeado
- `extra="allow"` en Pydantic models para forward compatibility

---

## 📌 Recommended Fix Order

1. **CRITICAL: Timeout en token exchange** — una línea, previene hang indefinido
2. **HIGH: auth_pkce en pyproject.toml** — el package instalado está roto sin esto
3. **HIGH: 4xx retry bug** — waste de tiempo + posibles side effects
4. **HIGH: load() bloquea startup** — mover a lazy/main init + timeout al SDK
5. **Merge PR #11** — corrige findings #6 y #7 de un golpe
6. **MEDIUM: async blocking** — `asyncio.to_thread` en llamadas al SDK
7. **MEDIUM: logging.basicConfig** — mover dentro de `main()`
8. Resto: cleanup, dead code, `global` innecesario

---

_Reviewers: GPT-5.5 ✅ · GLM-5.1 ✅ · DeepSeek V4 Pro ✅ · 3/3 completed_
