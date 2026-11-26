"""The buddy's engine: one MCP server and one asyncio loop on a background
thread, so the UI (main thread) never blocks. The UI submits requests; the
worker reports progress, tutorial steps and permission needs back through
callbacks that always run on the main thread."""
from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import Future

import anthropic
from mcp import ClientSession
from mcp.client.stdio import stdio_client
from PyObjCTools import AppHelper

from ..display import Printer, new_log_path
from ..harness import MODEL, _server_params
from ..planner import plan_and_run


class UISink(Printer):
    """Logs every event like the terminal does, and forwards it to the UI."""

    def __init__(self, ui, log_path: str):
        super().__init__(quiet=False, log_path=log_path)
        self.ui = ui

    def show(self, kind: str, data: dict):
        AppHelper.callAfter(self.ui.on_event, kind, data)


class Worker:
    def __init__(self, ui, model: str = MODEL, effort: str = "medium"):
        self.ui = ui
        self.model, self.effort = model, effort
        self.loop = asyncio.new_event_loop()
        self.session: ClientSession | None = None
        self.ready = threading.Event()
        self.error: str = ""
        self.current: asyncio.Task | None = None
        self.history: list[str] = []
        self.log_path = new_log_path("buddy")
        self.sink = UISink(ui, self.log_path)
        self._decision: asyncio.Future | None = None
        threading.Thread(target=self._thread, daemon=True, name="buddy-worker").start()

    # ------------------------------------------------------------ lifecycle
    def _thread(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._main())

    async def _main(self):
        try:
            async with stdio_client(_server_params()) as (r, w):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    self.session = session
                    self.client = anthropic.AsyncAnthropic()
                    self.ready.set()
                    await asyncio.Event().wait()  # keep the server alive for the app's lifetime
        except Exception as e:  # server failed to start
            inner = e
            while isinstance(inner, BaseExceptionGroup) and inner.exceptions:
                inner = inner.exceptions[0]
            self.error = f"{type(inner).__name__}: {inner}"
            self.ready.set()

    def reset_client(self):
        """The API key changed: make a new client with it."""
        self.loop.call_soon_threadsafe(lambda: setattr(self, "client", anthropic.AsyncAnthropic()))

    # -------------------------------------------------------------- requests
    def submit(self, text: str, show_me: bool = False, real_pointer: bool = False) -> Future:
        return asyncio.run_coroutine_threadsafe(self._run(text, show_me, real_pointer), self.loop)

    def stop(self):
        """Stop the current request (from the UI)."""
        def _cancel():
            if self._decision and not self._decision.done():
                self._decision.set_result("stop")
            if getattr(self, "_confirm", None) is not None and not self._confirm.done():
                self._confirm.set_result(False)
            if self.current and not self.current.done():
                self.current.cancel()
        self.loop.call_soon_threadsafe(_cancel)

    def decide(self, decision: str):
        """Tutorial answer from the UI: 'go' (do it for me), 'skip' (I did it), 'stop'."""
        def _set():
            if self._decision and not self._decision.done():
                self._decision.set_result(decision)
        self.loop.call_soon_threadsafe(_set)

    async def _run(self, text: str, show_me: bool, real_pointer: bool):
        self.ready.wait(30)
        if self.session is None:
            AppHelper.callAfter(self.ui.on_done, {"status": "error", "answer": "",
                                                  "evidence": f"Mac Buddy couldn't start its helper: {self.error}"})
            return
        await self.session.call_tool("harness_begin_task", {})
        self.sink.event("user", text=text)
        t0 = time.time()

        async def on_step(i: int, step: dict, frame: dict) -> str:
            if show_me:
                await self._bring_forward(step, frame)
                if not frame.get("w") and frame.get("app"):
                    # the window wasn't on screen when the step was planned: find it now
                    fr = await self.session.call_tool("harness_app_window_frame", {"app": frame["app"]})
                    import json as _j
                    try:
                        frame.update(_j.loads(fr.content[0].text) or {})
                    except (ValueError, IndexError, AttributeError):
                        pass
            self._decision = self.loop.create_future()
            AppHelper.callAfter(self.ui.show_step, i, step, frame, show_me, real_pointer)
            if not show_me:
                return "go"
            d = await self._decision
            self.sink.event("info", text=f"tutorial step {i + 1}: {d}", elapsed=time.time() - t0)
            AppHelper.callAfter(self.ui.hide_step)
            if d == "go":
                await self.session.call_tool("harness_begin_task", {})  # our own activation isn't a "focus move"
            return d

        async def on_permission(kind: str) -> bool:
            fut = self.loop.create_future()
            from . import perms
            AppHelper.callAfter(perms.ask, kind, lambda ok: self.loop.call_soon_threadsafe(fut.set_result, ok))
            return await fut

        from .. import terminal

        async def confirm_command(command: str, why: str, risky: list) -> bool:
            fut = self.loop.create_future()
            self._confirm = fut
            AppHelper.callAfter(self.ui.ask_command, command, why, risky,
                                lambda ok: self.loop.call_soon_threadsafe(
                                    lambda: fut.done() or fut.set_result(bool(ok))))
            try:
                return await fut
            finally:
                self._confirm = None
        terminal.confirm = confirm_command

        async def go():
            return await plan_and_run(text, self.session, self.client, self.sink, model=self.model,
                                      effort=self.effort, history="\n".join(self.history[-6:]),
                                      on_step=on_step if show_me else None, on_permission=on_permission)
        self.current = asyncio.ensure_future(go())
        try:
            res, run = await asyncio.wait_for(self.current, timeout=300)
            out = {"status": res.status, "answer": res.answer, "evidence": res.evidence,
                   "planned": not run.from_cache, "cached": run.from_cache, "fell_back": run.fell_back,
                   "model_calls": run.planning_calls + res.turns * run.fell_back,
                   "tokens": res.input_tokens + res.output_tokens, "seconds": time.time() - t0}
        except asyncio.CancelledError:
            out = {"status": "stopped", "answer": "", "evidence": "Stopped. Some steps may already have happened."}
        except asyncio.TimeoutError:
            out = {"status": "failed", "answer": "", "evidence": "This took longer than 5 minutes, so I stopped."}
        except anthropic.APIError as e:
            out = {"status": "error", "answer": "", "evidence": _friendly_api_error(e)}
        finally:
            self.current = None
        self.history.append(f"- Request: {text} → {out['status']}"
                            + (f": {out['answer']}" if out.get("answer") else ""))
        self.sink.event("summary", **{k: v for k, v in out.items() if k != "evidence"})
        AppHelper.callAfter(self.ui.on_done, out)

    async def _bring_forward(self, step: dict, frame: dict):
        """A tutorial needs the app on screen: bring the step's app forward (the person
        asked to be shown), then let the server's focus watch start afresh."""
        app = frame.get("app")
        from AppKit import NSWorkspace
        running = app and any(str(ra.localizedName() or "") == app
                              for ra in NSWorkspace.sharedWorkspace().runningApplications())
        if running:  # bring forward only: never launch it (that's the step's job, or the person's)
            proc = await asyncio.create_subprocess_exec("open", "-a", app)
            await proc.wait()
            await asyncio.sleep(0.5)
        await self.session.call_tool("harness_begin_task", {})


def _friendly_api_error(e: Exception) -> str:
    msg = str(e)
    if "credit balance" in msg:
        return "Your Anthropic account is out of credit. Add credit at console.anthropic.com, then try again."
    if "authentication" in msg.lower() or "401" in msg:
        return "Your API key wasn't accepted. Choose “API key…” in the Mac Buddy menu to enter it again."
    if "overloaded" in msg.lower() or "529" in msg:
        return "The AI service is busy right now. Try again in a minute."
    return f"Something went wrong talking to the AI service: {msg[:200]}"
