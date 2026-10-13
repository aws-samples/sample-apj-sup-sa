"""Daily room provisioning for the hosted deployment.

Each browser session gets its own short-lived private room. Rooms expire on their
own (``eject_at_room_exp``), so an abandoned tab cannot leave a bot sitting in a
call, and a leaked room URL stops working within the hour.
"""

from __future__ import annotations

import time

import aiohttp
from loguru import logger
from pipecat.transports.daily.utils import (
    DailyMeetingTokenParams,
    DailyMeetingTokenProperties,
    DailyRESTHelper,
    DailyRoomParams,
    DailyRoomProperties,
)

from talk2vid import config


class RoomBroker:
    """Creates one Daily room per session and cleans them up afterwards."""

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def _helper(self) -> DailyRESTHelper:
        if not config.DAILY_API_KEY:
            raise RuntimeError("DAILY_API_KEY is not set (required for transport=daily)")
        if self._session is None or self._session.closed:
            raise RuntimeError("room broker is not started")
        return DailyRESTHelper(
            daily_api_key=config.DAILY_API_KEY, aiohttp_session=self._session
        )

    async def create(self, label: str) -> dict:
        """A private room plus a bot token and a viewer token."""
        helper = self._helper()
        expires_at = time.time() + config.DAILY_ROOM_MINUTES * 60
        room = await helper.create_room(
            DailyRoomParams(
                privacy="private",
                properties=DailyRoomProperties(
                    exp=expires_at,
                    eject_at_room_exp=True,
                    enable_chat=False,
                    enable_prejoin_ui=False,
                    enable_emoji_reactions=False,
                    start_video_off=True,
                    # The bot and one human. Nobody else can join even with the URL.
                    max_participants=2,
                ),
            )
        )
        token_secs = config.DAILY_ROOM_MINUTES * 60
        bot_token = await helper.get_token(
            room.url,
            expiry_time=token_secs,
            owner=True,
            params=DailyMeetingTokenParams(
                properties=DailyMeetingTokenProperties(user_name="Talk2Vid", start_video_off=True)
            ),
        )
        user_token = await helper.get_token(
            room.url,
            expiry_time=token_secs,
            owner=False,
            eject_at_token_exp=True,
            params=DailyMeetingTokenParams(
                properties=DailyMeetingTokenProperties(user_name="Viewer", start_video_off=True)
            ),
        )
        logger.info(f"daily room created for {label}: {room.url}")
        return {"room_url": room.url, "bot_token": bot_token, "user_token": user_token}

    async def destroy(self, room_url: str) -> None:
        try:
            await self._helper().delete_room_by_url(room_url)
            logger.info(f"daily room deleted: {room_url}")
        except Exception as exc:
            # Rooms expire on their own, so this is housekeeping, not correctness.
            logger.debug(f"room cleanup skipped: {exc}")


broker = RoomBroker()
