"""
title: AnimeAPI Anime Image Generator
author: AnimeAPI
author_url: https://animeapi.ai
git_url: https://github.com/animeapi-ai/open-webui-animeapi
description: Generate anime images in chat with AnimeAPI at $0.003 per image. The model calls this tool when you ask for an anime picture; the image appears in the reply.
required_open_webui_version: 0.5.0
requirements: requests
version: 1.0.0
licence: MIT
"""

import asyncio
import base64
import time
from typing import Any, Awaitable, Callable, Literal, Optional

import requests
from pydantic import BaseModel, Field

PRICES = {"anime": 0.003, "ultra": 0.01, "ultra-max": 0.02}


class Tools:
    class Valves(BaseModel):
        api_key: str = Field(
            default="",
            description="AnimeAPI key (starts with ank_). Create one at https://animeapi.ai/dashboard/api-keys",
        )
        default_model: Literal["anime", "ultra", "ultra-max"] = Field(
            default="anime",
            description="Model used when the chat model does not pick one. anime $0.003, ultra $0.01, ultra-max $0.02 per image.",
        )
        allow_nsfw: bool = Field(
            default=False,
            description="Allow mature output. Off by default because chat output is often shared.",
        )
        enhance_prompt: bool = Field(
            default=True,
            description="Rewrite plain-English prompts into tags for the anime model.",
        )
        keep_image_in_chat: bool = Field(
            default=True,
            description="Store the image in the chat instead of linking to it. AnimeAPI image links are not permanent storage.",
        )
        base_url: str = Field(default="https://animeapi.ai", description="AnimeAPI base URL.")

    def __init__(self):
        self.valves = self.Valves()

    async def generate_anime_image(
        self,
        prompt: str,
        orientation: Literal["portrait", "square", "landscape"] = "portrait",
        model: Optional[Literal["anime", "ultra", "ultra-max"]] = None,
        __event_emitter__: Optional[Callable[[dict], Awaitable[Any]]] = None,
    ) -> str:
        """
        Generate one anime-style image with AnimeAPI and show it in the chat. Costs the user $0.003 per image on the default "anime" model ($0.01 on "ultra", $0.02 on "ultra-max"). Make one image per request unless the user asks for more.

        :param prompt: What to draw, in plain English: subject, appearance, action, setting, mood. Up to 1,000 characters.
        :param orientation: Shape of the image: "portrait", "square" or "landscape".
        :param model: Leave empty for the default. "anime" is best for characters; "ultra" follows long sentences and renders legible text; "ultra-max" has the highest detail.
        :return: A short report with the cost. The image itself is already shown to the user.
        """

        async def status(text: str, done: bool = False):
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": text, "done": done}})

        if not self.valves.api_key:
            await status("AnimeAPI key missing", done=True)
            return "The AnimeAPI tool has no API key. Ask the user to create one at https://animeapi.ai/dashboard/api-keys and paste it into this tool's settings (Valves)."

        chosen = model or self.valves.default_model
        if chosen not in PRICES:
            chosen = "anime"
        await status(f"Drawing with AnimeAPI ({chosen}, ${PRICES[chosen]} per image)...")

        try:
            data = await asyncio.to_thread(self._generate, prompt[:1000], chosen, orientation)
        except AnimeAPIError as e:
            await status("AnimeAPI could not make the image", done=True)
            return f"AnimeAPI error ({e.code}): {e}. {e.advice}"

        url = data["image_url"]
        shown = url
        if self.valves.keep_image_in_chat:
            try:
                shown = await asyncio.to_thread(self._as_data_uri, url)
            except Exception:
                shown = url  # fall back to the link

        if __event_emitter__:
            # A "files" event attaches the image to the assistant's reply and is saved with the chat.
            await __event_emitter__({"type": "files", "data": {"files": [{"type": "image", "url": shown}]}})
        await status(f"Image ready (${PRICES[chosen]})", done=True)

        balance = data.get("balance_usd")
        balance_text = f" Balance left: ${balance:.2f}." if isinstance(balance, (int, float)) else ""
        return (
            f"The image was generated and is already displayed to the user; do not repeat the image or its link. "
            f"Model: {chosen}, {orientation}. Cost: ${PRICES[chosen]}.{balance_text} "
            f"Briefly tell the user it is done and offer a variation."
        )

    def _generate(self, prompt: str, model: str, orientation: str) -> dict:
        body = {
            "prompt": prompt,
            "model": model,
            "orientation": orientation,
            "enhance": self.valves.enhance_prompt,
            "allowNSFW": self.valves.allow_nsfw,
        }
        headers = {"Authorization": f"Bearer {self.valves.api_key}", "User-Agent": "animeapi-open-webui/1.0"}
        last = AnimeAPIError("Unknown error", "UNKNOWN")
        for attempt in range(3):
            if attempt:
                time.sleep(2 * attempt)
            try:
                response = requests.post(
                    f"{self.valves.base_url.rstrip('/')}/api/generate", json=body, headers=headers, timeout=120
                )
            except requests.RequestException as e:
                last = AnimeAPIError(f"could not reach AnimeAPI ({e})", "NETWORK_ERROR")
                continue
            if response.ok:
                return response.json()
            try:
                err = response.json()
            except ValueError:
                err = {}
            last = AnimeAPIError(
                err.get("error") or f"HTTP {response.status_code}", err.get("code") or f"HTTP_{response.status_code}"
            )
            if not (err.get("retryable") or response.status_code in (500, 503)):
                break
        raise last

    def _as_data_uri(self, url: str) -> str:
        response = requests.get(url, timeout=120, headers={"User-Agent": "animeapi-open-webui/1.0"})
        response.raise_for_status()
        mime = response.headers.get("Content-Type", "image/webp").split(";")[0]
        return f"data:{mime};base64,{base64.b64encode(response.content).decode()}"


class AnimeAPIError(Exception):
    ADVICE = {
        "INVALID_KEY": "Ask the user to check the API key in this tool's settings.",
        "INSUFFICIENT_BALANCE": "Ask the user to add funds at https://animeapi.ai/dashboard/billing (from $5).",
        "MODERATION_BLOCKED": "The prompt was refused; do not retry it unchanged.",
        "PROVIDER_REJECTED": "The prompt was refused; do not retry it unchanged.",
        "RATE_LIMITED": "Wait a moment before trying again.",
    }

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code
        self.advice = self.ADVICE.get(code, "Trying once more is fine.")
