"""Harness adapter for OC Desktop Pet - Hanako 原生版。

从 Hanako 本体文件读取角色设定和模型配置:
  - identity.md / ishiki.md / description.md → 角色设定
  - provider-catalog.json → API 地址和密钥
  - memory/ → 记忆上下文注入

不再使用 skills/public/<角色>/SKILL.md 和 config.json 的独立配置。
"""
from __future__ import annotations

import collections
import json
import logging
import re
from pathlib import Path

import requests

from .hanako_context import HanakoContext
from .hanako_ws_client import HanakoUnavailableBeforeSend

logger = logging.getLogger(__name__)


class HanakoUnavailableAfterSend(Exception):
    """已经交给 Hanako 但尚未得到承诺回复 — 不要 fallback，会造成双执行。"""


class HanakoPetAdapter:
    """桌宠适配器:读取 Hanako 本体配置 → API 对话 → 返回回复

    完全依赖 HanakoContext 读取 Hanako 的同一套文件。
    不再保留独立的角色 prompt 和 API 配置。
    """

    def __init__(self, agent_id: str = "yuexinmiao", builtin: bool = False):
        self.agent_id = agent_id
        self._builtin = builtin
        self._context = HanakoContext(agent_id, builtin=builtin)

        # 读取模型配置 - .env 优先,回退到 Hanako, builtin 回退到 catalog 默认
        from env_config import get_llm_config
        env_llm = get_llm_config()
        if env_llm:
            self._base_url = env_llm["base_url"]
            self._api_key = env_llm["api_key"]
            self._model = env_llm["model"]
            self._api_type = "openai-completions"
            self._max_context = 0
            self._model_cfg = {"model": self._model}
            logger.info("LLM using .env override | model=%s", self._model)
        else:
            self._model_cfg = self._context.read_model_config()
            self._base_url = self._model_cfg.get("base_url", "")
            self._api_key = self._model_cfg.get("api_key", "")
            self._model = self._model_cfg.get("model", "")
            self._api_type = self._model_cfg.get("api_type", "openai-completions")
            self._max_context = self._model_cfg.get("max_context", 0)
            self._model_cfg = {"model": self._model}  # 统一属性名

            # builtin 角色没有 Hanako agent 目录，从 catalog 读默认 provider
            if builtin and (not self._base_url or not self._api_key):
                self._load_default_from_catalog()

        # 记忆预算: 优先使用用户配置，否则按模型 context 的 1% 计算
        from config import load_config
        config = load_config()
        memory_config = config.get('memory', {})
        
        user_budget = memory_config.get('budget_chars', 0)
        user_percent = memory_config.get('budget_percent', 1.0)
        
        if user_budget > 0:
            # 用户指定了固定字符数
            self._memory_budget = user_budget
            logger.info("Memory budget: %d chars (user configured)", self._memory_budget)
        elif self._max_context > 0:
            # 按模型 context 的百分比计算
            self._memory_budget = max(800, min(6000, int(self._max_context * user_percent / 100)))
            logger.info("Memory budget: %d chars (%.1f%% of %s)",
                         self._memory_budget, user_percent,
                         f"{self._max_context:,}" if self._max_context else "unknown")
        else:
            self._memory_budget = 800
            logger.info("Memory budget: %d chars (default)", self._memory_budget)

        # 构建 system prompt
        self._system_prompt = self._context.build_prompt()

        # 会话历史(内存)——有界 deque(maxlen=40)：append 原子（GIL），
        # 消除原 list + 读改写裁剪（self._history = self._history[-40:]）在
        # engine 后台线程与 idle_chatter 线程并发时的丢消息/交错问题（B2-7）。
        self._history: "collections.deque[dict]" = collections.deque(maxlen=40)

        # 验证
        missing = self._context.validate()
        if missing and not builtin:
            logger.warning("配置不完整,缺失: %s", ", ".join(missing))

        # ── M4: Hanako WS 传输模式 ──
        from env_config import get_hanako_config
        hanako_cfg = get_hanako_config()
        self.transport_mode: str = hanako_cfg["transport_mode"]
        self._reply_timeout: float = float(hanako_cfg["reply_timeout"])
        self._mirror_external_replies: bool = hanako_cfg["mirror_external_replies"]

        # 共享实例由 PetManager / ConversationEngine 注入；适配器不创建第二条 WS。
        self._session_manager = None
        logger.info("Hanako transport configured: %s", self.transport_mode)

        # 当前 Session 引用（由 PetManager / ConversationEngine 注入）
        self._current_session = None  # SessionRef | None (当前 agent 的)
        self._pinned_session_id = None  # 向后兼容：当前 agent 的 pin
        # M5: per-agent 会话保留 dict[agent_id -> session_id]（F3）
        # 切换 agent 时各自记住自己的 session，切回可续聊。
        self._agent_sessions: dict[str, object] = {}  # agent_id -> SessionRef
        self._agent_pinned: dict[str, str] = {}  # agent_id -> session_id

    def _load_default_from_catalog(self):
        """builtin 角色没有 Hanako agent，从 provider catalog 读默认模型"""
        import json
        from pathlib import Path
        catalog_path = Path.home() / ".hanako" / "provider-catalog.json"
        try:
            data = json.loads(catalog_path.read_text("utf-8"))
            providers = data.get("providers", {})
            # 优先用 agnes，其次第一个有 base_url 的 provider
            for prov_id in ["agnes"] + list(providers.keys()):
                prov = providers.get(prov_id, {})
                if prov.get("base_url") and prov.get("api_key"):
                    self._base_url = prov["base_url"]
                    self._api_key = prov["api_key"]
                    # 取第一个模型
                    models = prov.get("models", [])
                    if models:
                        m = models[0]
                        self._model = m.get("id", m) if isinstance(m, dict) else str(m)
                        self._max_context = m.get("context", 0) if isinstance(m, dict) else 0
                    self._api_type = prov.get("api", "openai-completions")
                    self._model_cfg = {"model": self._model}
                    logger.info("Builtin LLM from catalog: provider=%s model=%s", prov_id, self._model)
                    return
        except Exception as e:
            logger.warning("Failed to load default from catalog: %s", e)

        logger.info(
            "HanakoPetAdapter ready | agent=%s | model=%s | api=%s | prompt_len=%d",
            agent_id, self._model, self._base_url[:40] + "..." if self._base_url else "N/A",
            len(self._system_prompt),
        )

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    @property
    def model_config(self) -> dict:
        return dict(self._model_cfg)

    def chat_direct(self, message: str, inject_memory: bool = True, extra_context: str = "", tools: list = None, source: str = "user") -> tuple:
        """直接调用 LLM API（不走 Hanako WS） - 原 chat() 的完整实现

        由 chat() 路由器在内部来源（proactive/idle/memory_extract/memory_reflect/
        screen_enrich）、Hanako 不可用或 transport_mode==direct 时调用。

        source 标记消息来源：user（用户主动）/ proactive（桌宠主动搭话）/
        idle（闲置闲聊）/ memory_extract（事实抽取）/ memory_reflect（反思）/
        screen_enrich（屏幕感知标注）。proactive/idle 消息会加 [source] 前缀，
        让 LLM 能区分说话人，避免把桌宠自己的主动文案当成用户消息计入上下文。

        历史写入：仅 ``user`` 来源写入 ``self._history``（本地上下文 deque）。
        内部来源（proactive/idle/memory_extract/memory_reflect/screen_enrich）
        不写历史——否则抽取/反思/主动文案会污染后续对话注入的上下文
        （``list(self._history)[-10:]``），QA 实测确认 memory 来源会污染。
        """
        if not self._base_url or not self._api_key:
            return "...(模型未配置,请在设置中配置模型)", "neutral"

        # 来源标记：proactive/idle 加 [source] 前缀；user 保持原样（不破坏现有 prompt 结构）
        user_content = message.strip()
        if source in ("proactive", "idle"):
            user_content = f"[{source}] {user_content}"

        messages = [{"role": "system", "content": self._system_prompt + "\n\n[输出规则] 1. 回复简短自然，不超过 2 句话。2. 在回复中嵌入情绪标签，格式 [emotion:xxx]，可选值：happy/sad/angry/surprised/thinking/neutral/cute/missing。可以在句末或句中。例如：'你回来啦！[emotion:happy]' 或 '[emotion:thinking]让我想想……'"}]

        # 注入记忆
        if inject_memory:
            memory_text = self._context.build_memory_context(max_chars=self._memory_budget)
            if memory_text:
                messages.append({
                    "role": "system",
                    "content": f"[以下是你当前的记忆和状态,请自然参考--不要逐字复述,可以作为话题延续的线索]\n{memory_text}",
                })

        # 注入感知上下文(时间/情绪/日程)
        if extra_context:
            messages.append({
                "role": "system",
                "content": extra_context,
            })

        # 追加最近对话历史(最多 10 轮)——deque 不支持切片，先转 list
        for turn in list(self._history)[-10:]:
            messages.append(turn)

        messages.append({"role": "user", "content": user_content})

        try:
            import time as _t
            _t0 = _t.monotonic()
            resp = self._call_api(messages, tools=tools)
            _elapsed = _t.monotonic() - _t0
            # BugFix #4：显式记录慢 LLM 调用（>8s），便于区分"模型 inference 慢"
            # 与"prompt/build_context 拖慢"（用户体感"思考中卡 27s"的根因排查）。
            if _elapsed > 8.0:
                logger.warning(
                    "LLM 调用偏慢: %.1fs | model=%s | prompt_tokens≈%d | source=%s",
                    _elapsed, self._model,
                    sum(len(m.get("content") or "") for m in messages), source,
                )

            # 检查是否是 tool_calls 响应
            if isinstance(resp, dict) and resp.get("tool_calls"):
                # 保存用户消息到历史（仅用户真实对话）
                if self._records_history(source):
                    self._history.append({"role": "user", "content": user_content})
                return resp, None  # 返回 tool_calls 给调用方处理

            text = resp.strip() if resp and resp.strip() else ""

            # 兜底：检查 content 里是否包含 <function> 标签（非标准 tool calling）
            if text and tools:
                parsed = self._parse_function_in_content(text)
                if parsed:
                    logger.info("Parsed tool call from content (non-standard)")
                    if self._records_history(source):
                        self._history.append({"role": "user", "content": user_content})
                    return {"tool_calls": parsed, "message": {"content": text}}, None

            if not text:
                logger.warning("LLM returned empty: %s", repr(resp[:100] if resp else None))
                text = "(......想不起来要说什么了)"
                emotion = "thinking"
                if self._records_history(source):
                    self._history.append({"role": "user", "content": user_content})
                    self._history.append({"role": "assistant", "content": text})
                return text, emotion

            # 解析情绪标签（匹配全文，支持多个，取最后一个）
            text, emotion = self.parse_emotion(text)

            # 保存到历史（仅用户真实对话；内部来源不写，避免污染上下文）
            if self._records_history(source):
                self._history.append({"role": "user", "content": user_content})
                self._history.append({"role": "assistant", "content": text})

            return text, emotion
        except requests.exceptions.Timeout:
            logger.warning("LLM timeout")
            return "(网络有点慢,你再说一遍?)", "neutral"
        except requests.exceptions.ConnectionError:
            logger.warning("LLM connection error")
            return "(连不上--检查一下网络配置吧)", "sad"
        except requests.exceptions.HTTPError as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status == 401:
                # 精确区分凭证来源，避免用户盲目改 .env
                _url = getattr(getattr(e, "response", None), "url", "") or ""
                _host = ""
                try:
                    from urllib.parse import urlparse
                    _host = (urlparse(_url).netloc or "").lower()
                except Exception:
                    _host = ""
                if "hanako" in _host or "xiaomimi" in _host or "token-plan" in _host:
                    logger.warning(
                        "LLM 401 — Hanako/TokenPlan 凭证失效，请重新登录 Hanako "
                        "刷新 server-info.json / provider-catalog.json"
                    )
                    return "(API 凭证失效了，重新登录一下 Hanako 就行)", "neutral"
                logger.warning("LLM 401 — .env 的 LLM_API_KEY 失效，请更新 .env")
                return "(API 凭证失效了，检查下 .env 的 LLM_API_KEY)", "neutral"
            logger.warning("LLM HTTP error: %s", e)
            return "(出了点岔子)", "neutral"
        except Exception as e:
            logger.warning("Chat failed: %s", e)
            return "(出了点岔子)", "neutral"

    @staticmethod
    def _records_history(source: str) -> bool:
        """是否把本轮写入本地 ``self._history``（后续对话会注入该上下文）。

        仅用户真实对话（source 为空/未指定/显式 "user"）写历史；
        内部来源（proactive/idle/memory_extract/memory_reflect/screen_enrich）
        不写——抽取/反思/主动文案作为系统指令发给 LLM，不应进入对话上下文。
        """
        return source in (None, "", "user")

    def chat(self, message: str, inject_memory: bool = True, extra_context: str = "", tools: list = None, source: str = "user") -> tuple:
        """入口路由 - 根据 transport_mode 和 source 选择路径

        - user 消息：走 Hanako session（工具、记忆、多轮）
        - 内部来源（proactive/idle/memory_extract/memory_reflect/screen_enrich）：
          直接走 LLM API（轻量快速，不占 session、不写本地 _history）
        """
        # 内部来源（非用户真实对话）：一律本地 LLM 直连，绝不进 Hanako session——
        # proactive/idle：主动搭话/闲置闲聊；
        # memory_extract/memory_reflect：事实抽取/反思（prompt 以 user 身份发进
        #   Hanako 会话会污染 display_text 历史，QA 实测确认）；
        # screen_enrich：屏幕感知语义化标注（pet.py 已显式走 chat_direct，设计意图如此）。
        # chat_direct 内部对非 user 来源不写 self._history（见 _records_history），
        # 避免抽取/反思/主动文案污染本地上下文（deque 会被注入后续对话）。
        if source in ("proactive", "idle", "memory_extract", "memory_reflect", "screen_enrich"):
            return self.chat_direct(message, False, extra_context, tools=None, source=source)

        # direct 模式：跳过 Hanako
        if self.transport_mode == "direct":
            return self.chat_direct(message, inject_memory, extra_context, tools)

        # Hanako 模式：先尝试 Hanako，失败再考虑 fallback
        try:
            return self.chat_via_hanako(message, inject_memory, extra_context, tools)
        except HanakoUnavailableBeforeSend as e:
            logger.warning("Hanako 不可用（send 前）: %s", e)
            if self.transport_mode == "prefer_hanako":
                logger.info("Fallback -> chat_direct")
                return self.chat_direct(message, inject_memory, extra_context, tools)
            # hanako_only：不允许 fallback
            raise
        except HanakoUnavailableAfterSend as e:
            # 已交给 Hanako，绝不 fallback - 避免双执行
            logger.error("Hanako 已接收但未完成，不能 fallback: %s", e)
            return "…", "neutral"

    def chat_via_hanako(
        self,
        message: str,
        inject_memory: bool = True,
        extra_context: str = "",
        tools: list = None,
        timeout: float = None,
        source: str = "user",
    ) -> tuple:
        """通过 Hanako WS Session 发送消息

        Fallback 边界：
        - send_and_wait() 之前失败 -> raise HanakoUnavailableBeforeSend（chat() 可 fallback）
        - send_and_wait() 之后失败 -> raise HanakoUnavailableAfterSend（绝不能 fallback）

        source 通过 ui_context 透传给 Hanako（proactive/idle/user），
        同时保留 client="oc-pet" 标识来源客户端。
        """
        if self._session_manager is None:
            raise HanakoUnavailableBeforeSend(
                "HanakoSessionManager 未注入（请检查 core/hanako_session_manager.py 是否存在）"
            )
        sm = self._session_manager
        if not hasattr(sm, "send_and_wait"):
            raise HanakoUnavailableBeforeSend("HanakoSessionManager 未实例化")
        if self._current_session is None:
            try:
                aid = self.agent_id
                # F3: 优先复用该 agent 已 pin 的 session（切回续聊）
                pinned = self._agent_pinned.get(aid)
                if pinned:
                    # 有钉住的 session，复用
                    self._current_session = sm.ensure_session(
                        agent_id=aid,
                        preferred_session_id=pinned
                    )
                elif aid in self._agent_sessions:
                    # 内存里已有该 agent 的 session 引用，直接复用
                    self._current_session = self._agent_sessions[aid]
                else:
                    # 首次：为每个桌宠/agent 创建专属 session
                    self._current_session = sm.create_session(agent_id=aid)
                self._agent_sessions[aid] = self._current_session
                self._agent_pinned[aid] = getattr(self._current_session, 'session_id', None)
                self._pinned_session_id = self._agent_pinned.get(aid)
            except Exception as e:
                raise HanakoUnavailableBeforeSend("无法准备 Hanako Session") from e

        # 拼装 text：extra_context 作为前缀附加（Hanako 自己管记忆，inject_memory 被忽略）
        text = message.strip()
        if extra_context and extra_context.strip():
            text = f"[pet-context]\n{extra_context.strip()}\n[/pet-context]\n\n{text}"

        import time as _time
        max_retries = 3
        retry_delay = 2.0  # 秒
        for attempt in range(max_retries):
            try:
                result = sm.send_and_wait(
                    self._current_session,
                    text,
                    timeout=timeout if timeout is not None else self._reply_timeout,
                    display_text=message.strip(),
                    ui_context={"source": source, "client": "oc-pet", "agentId": self.agent_id},
                )
                break  # 成功
            except HanakoUnavailableBeforeSend:
                raise
            except Exception as e:
                err_msg = str(e)
                if "pending turn" in err_msg.lower() and attempt < max_retries - 1:
                    logger.info("Session busy, retry %d/%d in %.1fs", attempt + 1, max_retries, retry_delay)
                    # 重试仍在忙：可能是服务端 turn 清理卡住/“半个发送”挂了锁。
                    # 尝试强制中断当前 turn，释放服务端锁，避免 session 永久卡死
                    # （不中断则后续所有消息都会持续撞 busy）。
                    if attempt >= 1:
                        try:
                            self._session_manager.abort(self._current_session, "busy_reset")
                        except Exception:
                            pass
                    _time.sleep(retry_delay)
                    retry_delay *= 1.5  # 递增等待
                    continue
                logger.error("Hanako send_and_wait 异常: %s", e)
                raise HanakoUnavailableAfterSend(f"send_and_wait raised: {e}") from e

        if getattr(result, "error", None):
            # Bug A 兜底：turn 失败（超时/异常）但工具确实在云端执行了——
            # 用已记录的工具结果合成一句可显示的回复，避免用户只看到"…"。
            # 注意这不是 fallback 到本地 LLM（不会双执行），只是把云端工具结果上屏。
            synthesized = self._synthesize_reply_from_tools(
                getattr(result, "tool_calls", ()) or ()
            )
            if synthesized:
                logger.info("Hanako turn 失败但工具已执行，用工具结果兜底: %s", synthesized[:60])
                return synthesized, "neutral"
            raise HanakoUnavailableAfterSend(f"reply error: {result.error}")
        if getattr(result, "aborted", False):
            return "(对话被打断了)", "neutral"

        reply_text = (getattr(result, "text", "") or "").strip()
        cleaned, emotion = self.parse_emotion(reply_text)
        if not cleaned or cleaned in ("…", "...", "..."):
            # Bug A 修复：工具轮若最终文本为空/“…”（WS 没送达最终回复，或服务端
            # 工具链把最终文本放在 tool_end 的 details 里），用最后成功的工具结果
            # 合成一句有意义的回复，避免桌宠只显示“…”的空话。
            synthesized = self._synthesize_reply_from_tools(
                getattr(result, "tool_calls", ()) or ()
            )
            if synthesized:
                cleaned = synthesized
                if emotion == "neutral":
                    emotion = "happy"
        if not cleaned:
            cleaned = "…"
            if emotion == "neutral":
                emotion = "thinking"

        # 截断：桌宠只展示摘要（完整回复在 Hanako 主窗口可见）
        if len(cleaned) > 60:
            # 取第一个句子（。！？\n），或前 40 字
            for sep in ['\n', '。', '！', '？', '.', '!', '?']:
                idx = cleaned.find(sep, 10)  # 从第 10 字开始找，避免太短
                if 0 < idx < 60:
                    cleaned = cleaned[:idx + 1]
                    break
            else:
                cleaned = cleaned[:40] + "…"
            logger.info("Reply truncated for bubble: %s", cleaned[:50])

        # Hanako 已经执行过 result.tool_calls，绝不能交给桌宠本地再执行一次。
        # 同步本地 history（向后兼容）——deque(maxlen=40) 自动裁剪，无需读改写
        try:
            self._history.append({"role": "user", "content": message.strip()})
            self._history.append({"role": "assistant", "content": cleaned})
        except Exception:
            pass

        return cleaned, emotion

    @staticmethod
    def _synthesize_reply_from_tools(tool_calls) -> str:
        """工具轮最终文本缺失时，从工具执行结果合成一句可显示的回复。

        取最后一条成功（phase=end 且 success 非 False）的工具：
          - 优先用 tool_end 事件 details 里的结果文本（服务端常把工具输出放这里）
          - 其次用工具名生成"已完成「xx」"占位
        返回空串表示无可合成内容（调用方回退默认）。
        """
        if not tool_calls:
            return ""
        for tc in reversed(list(tool_calls)):
            if not isinstance(tc, dict):
                continue
            if tc.get("phase") == "end" and tc.get("success") is False:
                continue
            name = str(tc.get("name") or tc.get("tool") or "")
            details = tc.get("details")
            if isinstance(details, dict):
                text = str(details.get("text") or details.get("content") or "").strip()
                if text:
                    return text[:120]
            elif isinstance(details, str) and details.strip():
                return details.strip()[:120]
            if name:
                return f"已为你完成「{name}」"
        return ""

    @staticmethod
    def parse_emotion(text: str) -> tuple:
        """从文本解析 [emotion:xxx]，返回 (cleaned_text, emotion)

        全文匹配所有 [emotion:xxx]，取最后一个出现的 emotion。
        额外剥离：agent 思考/MOOD 块（[ Vibe: ... ] / 纯文本 "Vibe:" 等），
        避免气泡显示内部思考。
        """
        if not text:
            return "", "neutral"
        # 支持 [emotion:xxx] / [emotion: xxx] / [ emotion : xxx ] 等 LLM 常见变体
        em_matches = re.findall(r"\[\s*emotion\s*:\s*(\w+)\s*\]", text, flags=re.IGNORECASE)
        emotion = em_matches[-1].lower() if em_matches else "neutral"
        cleaned = re.sub(r"\s*\[\s*emotion\s*:\s*\w+\s*\]\s*", " ", text, flags=re.IGNORECASE)
        # BugFix #4：整段剥离 <mood>...</mood> 内省块（Vibe/Reflections/Will/
        # Sparks 字段是给服务端/记忆用的元数据，不是给用户的回复）——必须在
        # HTML 剥离之前做，否则 <mood> 标签被剥掉后只剩 Vibe 文本。
        cleaned = re.sub(r"<mood>.*?</mood>", " ", cleaned, flags=re.IGNORECASE | re.DOTALL)
        # 剥离 agent 思考/MOOD 块：以 [ Vibe:/Sparks:/Reflections:/Will: 开头的成块内容。
        # 先剥闭合块（到 ] 为止，可跨行），再剥未闭合残余（到文本末尾）。
        # 注意不能依赖 MULTILINE 的 $ 作边界（会在块内第一行行尾提前停下）。
        cleaned = re.sub(
            r"^\[\s*(?:Vibe|Sparks|Reflections|Will)\b[\s\S]*?\]",
            "", cleaned, flags=re.IGNORECASE | re.MULTILINE | re.DOTALL
        )
        cleaned = re.sub(
            r"^\[\s*(?:Vibe|Sparks|Reflections|Will)\b[\s\S]*$",
            "", cleaned, flags=re.IGNORECASE | re.MULTILINE | re.DOTALL
        ).strip()
        # BugFix #4：剥离纯文本（无方括号）形式的 mood 前缀块——LLM 输出或
        # 历史恢复常以 "Vibe: ..." / "Reflections: ..." / "Will: ..." /
        # "Sparks: ..." 开头（一行或多行），全部剥到该段末尾，不上气泡。
        cleaned = re.sub(
            r"(?im)^\s*(?:Vibe|Sparks|Reflections|Will)\s*[:：][^\n]*(?:\n|$)",
            "", cleaned,
        )
        # 剥离表情包 XML 段：<parameter name=...>值</parameter> 整段剥掉
        cleaned = re.sub(r"<parameter[^>]*>.*?</parameter>", "", cleaned, flags=re.S)
        # 剥离其余残留标签（<brioqingbao_express> 等）
        cleaned = re.sub(r"<[^>]+>", "", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return cleaned, emotion

    def set_session(self, session_ref) -> None:
        """注入当前 Session 引用（PetManager / ConversationEngine 调用）"""
        self._current_session = session_ref
        sid = getattr(session_ref, 'session_id', None) if session_ref else None
        self._pinned_session_id = sid
        # 按 agent 维度记录，供切回续聊
        if sid and self.agent_id:
            self._agent_sessions[self.agent_id] = session_ref
            self._agent_pinned[self.agent_id] = sid

    def switch_agent(self, agent_id: str) -> bool:
        """切换对话后端 agent（F2/F4）。

        - 更新 self.agent_id
        - 尝试恢复该 agent 已 pin 的 session（切回续聊）
        - 若该 agent 已有历史，清空本地 _history（避免串味）
        - 不动本地显示角色（立绘/皮肤）

        Returns:
            True 切换成功；False 参数非法（空 agent_id）
        """
        if not agent_id or not str(agent_id).strip():
            return False
        agent_id = str(agent_id).strip()
        if agent_id == self.agent_id:
            return True  # 已是目标 agent，无需切换
        self.agent_id = agent_id
        # 切走时把当前 session 存好（已在 _agent_sessions 里）
        # 恢复目标 agent 的 session（若有）
        self._current_session = self._agent_sessions.get(agent_id)
        self._pinned_session_id = self._agent_pinned.get(agent_id)
        # 切换后本地历史是上一 agent 的，清空防串味（服务端会话各自独立）
        self._history.clear()
        logger.info("adapter 切换 agent: %s (恢复session=%s)",
                    agent_id, self._pinned_session_id or "无")
        return True

    def set_session_manager(self, manager) -> None:
        """注入 SessionManager 实例（覆盖延迟导入的类引用）"""
        self._session_manager = manager

    def _parse_function_in_content(self, text: str) -> list:
        """从 content 文本中解析 <function> 标签格式的工具调用

        支持格式：
            <function=tool_name>{"arg": "value"}</function>
            <function=name>args_json</function>
        """
        pattern = r'<function=([a-zA-Z0-9_-]+)[^>]*>(.*?)</function>'
        matches = re.findall(pattern, text, re.DOTALL)
        if not matches:
            return []

        tool_calls = []
        for name, args_str in matches:
            args_str = args_str.strip()
            try:
                json.loads(args_str)  # 验证 JSON
            except json.JSONDecodeError:
                args_str = '{}'

            tool_calls.append({
                "id": f"call_{name}_{len(tool_calls)}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": args_str,
                }
            })
        return tool_calls

    def _call_api(self, messages: list[dict], tools: list = None):
        """调用 LLM API

        支持两种 API 类型:
          - openai-completions: POST /chat/completions
          - anthropic-messages: POST /messages
        """
        if self._api_type == "anthropic-messages":
            return self._call_anthropic(messages)
        else:
            return self._call_openai(messages, tools=tools)

    def _call_openai(self, messages: list[dict], tools: list = None):
        """调用 OpenAI 兼容 API"""
        base = self._base_url.rstrip('/')
        # 自动补 /v1 前缀（如果用户填的是裸域名）
        if not base.endswith('/v1') and '/v1/' not in base:
            base += '/v1'
        url = f"{base}/chat/completions"
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self._model,
                "messages": messages,
                "temperature": 0.7,
                **({"tools": tools} if tools else {}),
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            logger.warning("API returned no choices: %s", json.dumps(data, ensure_ascii=False)[:200])
            return ""

        message = choices[0].get("message", {})
        finish = choices[0].get("finish_reason", "")

        # 检查 tool_calls
        tool_calls = message.get("tool_calls")
        if tool_calls:
            logger.info("LLM requested %d tool call(s) | finish=%s", len(tool_calls), finish)
            return {"tool_calls": tool_calls, "message": message}

        content = message.get("content", "")
        if not content:
            logger.warning("API returned empty content | finish=%s | usage=%s", finish, data.get("usage", {}))
            return ""
        logger.info("API OK | finish=%s | usage=%s", finish, data.get("usage", {}))
        return content.strip()

    def _call_anthropic(self, messages: list[dict]) -> str:
        """调用 Anthropic 兼容 API"""
        url = f"{self._base_url.rstrip('/')}/messages"

        # 分离 system 消息
        system_content = ""
        api_messages = []
        for m in messages:
            if m["role"] == "system":
                system_content += m["content"] + "\n"
            else:
                api_messages.append(m)

        payload = {
            "model": self._model,
            "messages": api_messages,
            "max_tokens": 300,
            "temperature": 0.7,
        }
        if system_content.strip():
            payload["system"] = system_content.strip()

        resp = requests.post(
            url,
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["content"][0]["text"].strip()

    def reset_history(self):
        """清空对话历史"""
        self._history.clear()
