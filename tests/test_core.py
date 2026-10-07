from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from briefing.sources.canadabuys import select_notices
from briefing.sources.market_scan import items_from_search_response
from briefing.deliver.email import email_recipients
from briefing.deliver.telegram import telegram_chat_ids
from briefing.models import Digest, DigestItem, DigestSection, Item
from briefing.render import assemble, digest_from_markdown, render_markdown, render_telegram
from briefing.schedule import select_slot
from briefing.sources import cap_candidates
from briefing.state import State
from briefing.textutil import match_keywords, normalize_url


TORONTO = {
    "timezone": "America/Toronto",
    "local_hour": 7,
    "afternoon_enabled": False,
    "afternoon_hour": 16,
}


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


class ScheduleTests(unittest.TestCase):
    def test_winter_morning_is_12_utc(self) -> None:
        self.assertEqual(select_slot(_utc(2026, 1, 15, 12), TORONTO, {}, force=False), "morning")
        self.assertIsNone(select_slot(_utc(2026, 1, 15, 11), TORONTO, {}, force=False))

    def test_summer_morning_is_11_utc(self) -> None:
        self.assertEqual(select_slot(_utc(2026, 7, 15, 11), TORONTO, {}, force=False), "morning")

    def test_second_utc_run_does_not_duplicate_a_delivered_slot(self) -> None:
        delivered = {"2026-07-15-morning": "2026-07-15T11:05:00+00:00"}
        self.assertIsNone(select_slot(_utc(2026, 7, 15, 12), TORONTO, delivered, force=False))

    def test_job_started_hours_late_still_sends_the_morning(self) -> None:
        # 14:50 UTC in October is 10:50 EDT, about where GitHub starts a 7:17 trigger.
        self.assertEqual(select_slot(_utc(2026, 10, 7, 14, 50), TORONTO, {}, force=False), "morning")
        self.assertEqual(select_slot(_utc(2026, 1, 15, 20, 30), TORONTO, {}, force=False), "morning")
        self.assertIsNone(select_slot(_utc(2026, 1, 15, 21, 30), TORONTO, {}, force=False))

    def test_runs_before_the_morning_hour_wait(self) -> None:
        self.assertIsNone(select_slot(_utc(2026, 10, 7, 10, 50), TORONTO, {}, force=False))

    def test_afternoon_stays_off_until_enabled(self) -> None:
        self.assertIsNone(select_slot(_utc(2026, 7, 15, 20), TORONTO, {}, force=False))
        enabled = {**TORONTO, "afternoon_enabled": True}
        self.assertEqual(select_slot(_utc(2026, 7, 15, 20), enabled, {}, force=False), "afternoon")
        self.assertEqual(select_slot(_utc(2026, 1, 15, 21), enabled, {}, force=False), "afternoon")
        delivered = {"2026-01-15-morning": "2026-01-15T12:20:00+00:00"}
        self.assertIsNone(select_slot(_utc(2026, 1, 15, 20), enabled, delivered, force=False))

    def test_force_always_runs(self) -> None:
        self.assertEqual(select_slot(_utc(2026, 1, 15, 3), TORONTO, {}, force=True), "manual")

    def test_forced_run_in_the_morning_counts_as_the_morning(self) -> None:
        self.assertEqual(select_slot(_utc(2026, 10, 7, 14), TORONTO, {}, force=True), "morning")
        delivered = {"2026-10-07-morning": "2026-10-07T14:00:00+00:00"}
        self.assertEqual(select_slot(_utc(2026, 10, 7, 15), TORONTO, delivered, force=True), "manual")


class StateTests(unittest.TestCase):
    def test_round_trip_and_prune(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "seen.json"
            state = State(path, {}, {})
            now = datetime(2026, 10, 5, tzinfo=timezone.utc)
            state.mark_urls(["https://example.com/a?utm_source=x"], now)
            state.mark_urls(["https://example.com/old"], now - timedelta(days=45))
            state.mark_delivered("2026-10-05-morning", now)
            state.prune(now, days=30)
            state.save()

            loaded = State.load(path)
            self.assertTrue(loaded.has_url("https://example.com/a"))
            self.assertFalse(loaded.has_url("https://example.com/old"))
            self.assertIn("2026-10-05-morning", loaded.delivered)

    def test_normalize_strips_tracking_params(self) -> None:
        self.assertEqual(
            normalize_url("https://Example.com/Story/?utm_source=hn&id=1"),
            normalize_url("https://example.com/Story?id=1"),
        )


class CanadaSourceTests(unittest.TestCase):
    def test_canadabuys_keeps_it_notices_and_ranks_ontario_first(self) -> None:
        since = datetime(2026, 9, 1, tzinfo=timezone.utc)
        rows = [
            {
                "title-titre-eng": "Road salt",
                "publicationDate-datePublication": "2026-10-01",
                "noticeURL-URLavis-eng": "https://canadabuys.canada.ca/salt",
                "tenderDescription-descriptionAppelOffres-eng": "Supply of road salt.",
                "contractingEntityAddressProvince-entiteContractanteAdresseProvince-eng": "ON",
            },
            {
                "title-titre-eng": "Software support for schools",
                "publicationDate-datePublication": "2026-10-02",
                "noticeURL-URLavis-eng": "https://canadabuys.canada.ca/software",
                "tenderDescription-descriptionAppelOffres-eng": "Education software maintenance.",
                "contractingEntityAddressProvince-entiteContractanteAdresseProvince-eng": "BC",
            },
            {
                "title-titre-eng": "Cloud services",
                "publicationDate-datePublication": "2026-10-04",
                "noticeURL-URLavis-eng": "https://canadabuys.canada.ca/cloud",
                "tenderDescription-descriptionAppelOffres-eng": "Cloud hosting for a department.",
                "contractingEntityAddressProvince-entiteContractanteAdresseProvince-eng": "Ontario",
            },
            {
                "title-titre-eng": "Spectroradiometers",
                "publicationDate-datePublication": "2026-10-05",
                "noticeURL-URLavis-eng": "https://canadabuys.canada.ca/spectro",
                "tenderDescription-descriptionAppelOffres-eng": "Units with accessories and software.",
                "unspscDescription-eng": "Spectroscopic equipment",
            },
            {
                "title-titre-eng": "Old software contract",
                "publicationDate-datePublication": "2026-01-01",
                "noticeURL-URLavis-eng": "https://canadabuys.canada.ca/old",
                "tenderDescription-descriptionAppelOffres-eng": "Software.",
            },
        ]
        chosen = select_notices(rows, keywords=["software", "cloud", "education"], since=since, limit=10)
        self.assertEqual(
            [item.title for item in chosen],
            ["Cloud services", "Software support for schools"],
        )

    def test_canadabuys_builds_a_notice_url_from_the_reference_number(self) -> None:
        since = datetime(2026, 9, 1, tzinfo=timezone.utc)
        chosen = select_notices(
            [
                {
                    "title-titre-eng": "Software architect",
                    "publicationDate-datePublication": "2026-10-05",
                    "referenceNumber-numeroReference": "cb-965-17321735",
                    "noticeURL-URLavis-eng": "",
                    "tenderDescription-descriptionAppelOffres-eng": "Drupal software support.",
                }
            ],
            keywords=["software"],
            since=since,
            limit=5,
        )
        self.assertEqual(
            chosen[0].url,
            "https://canadabuys.canada.ca/en/tender-opportunities/tender-notice/cb-965-17321735",
        )

    def test_market_scan_keeps_only_searched_urls(self) -> None:
        response = SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="text",
                    citations=[
                        SimpleNamespace(
                            type="web_search_result_location",
                            url="https://example.com/rfp",
                            title="Board RFP",
                            cited_text="A school board posted a technology RFP.",
                        ),
                        SimpleNamespace(
                            type="char_location",
                            url="https://example.com/invented",
                            title="Nope",
                            cited_text="Invented.",
                        ),
                    ],
                ),
                SimpleNamespace(
                    type="web_search_tool_result",
                    content=[
                        SimpleNamespace(
                            type="web_search_result",
                            url="https://example.com/tender",
                            title="Ontario tender",
                            page_age="2 days ago",
                        )
                    ],
                ),
            ]
        )
        items = items_from_search_response(response, limit=8)
        self.assertEqual(
            [item.url for item in items],
            ["https://example.com/rfp", "https://example.com/tender"],
        )
        self.assertEqual(items[0].source, "Market scan")


class RecipientTests(unittest.TestCase):
    def test_email_list_combines_config_and_env_without_duplicates(self) -> None:
        os.environ["EMAIL_TO"] = "You@Example.com, other@example.com"
        try:
            recipients = email_recipients(
                {"delivery": {"email_to": ["colleague@example.com", "you@example.com"]}}
            )
        finally:
            os.environ.pop("EMAIL_TO", None)
        self.assertEqual(
            recipients,
            ["colleague@example.com", "you@example.com", "other@example.com"],
        )

    def test_telegram_chat_ids_can_be_several(self) -> None:
        os.environ["TELEGRAM_CHAT_ID"] = "111, 222"
        try:
            chats = telegram_chat_ids({"delivery": {"telegram_chat_ids": ["222", "333"]}})
        finally:
            os.environ.pop("TELEGRAM_CHAT_ID", None)
        self.assertEqual(chats, ["111", "222", "333"])


class CandidateCapTests(unittest.TestCase):
    def test_high_scores_cannot_crowd_out_hacker_news(self) -> None:
        items = [
            Item(
                title=f"model {index}",
                url=f"https://huggingface.co/org/model-{index}",
                source="Hugging Face trending",
                section="ai",
                score=5000,
            )
            for index in range(15)
        ]
        items += [
            Item(
                title=f"Story {index}",
                url=f"https://example.com/hn-{index}",
                source="Hacker News",
                section="ai",
                score=10,
            )
            for index in range(10)
        ]
        chosen = cap_candidates(items, _limits())
        hacker_news = [item for item in chosen if item.source == "Hacker News"]
        self.assertGreaterEqual(len(hacker_news), 6)

    def test_watchlist_items_are_reserved(self) -> None:
        items = [
            Item(
                title=f"noise {index}",
                url=f"https://example.com/noise-{index}",
                source="Hacker News",
                section="ai",
                score=100,
            )
            for index in range(10)
        ]
        items.append(
            Item(
                title="ROCm progress",
                url="https://example.com/rocm",
                source="r/LocalLLM",
                section="ai",
                score=1,
                watchlist=["ROCm"],
            )
        )
        chosen = cap_candidates(items, _limits())
        self.assertTrue(any(item.watchlist for item in chosen))


def _limits() -> dict:
    return {
        "limits": {
            "max_ai_candidates": 12,
            "max_canada_candidates": 5,
            "max_watchlist_candidates": 2,
            "ai_quotas": {"hackernews": 6, "huggingface": 4, "reddit": 4, "github": 2},
            "canada_quotas": {},
        }
    }


class BriefingAssemblyTests(unittest.TestCase):
    def test_unknown_ids_are_dropped_and_sections_stay_in_order(self) -> None:
        candidates = [
            Item(title="Real", url="https://example.com/real", source="Hacker News", section="ai", item_id="ai-1"),
            Item(
                title="Board",
                url="https://example.com/board",
                source="Google News",
                section="canada",
                item_id="ca-1",
                watchlist=["PowerSchool"],
            ),
        ]
        parsed = SimpleNamespace(
            subject="AI + Ontario briefing — October 5, 2026",
            intro="Two items.",
            sections=[
                SimpleNamespace(
                    id="canada",
                    items=[
                        SimpleNamespace(id="ca-1", summary="A board item.", why_it_matters="Talk to clients."),
                        SimpleNamespace(id="made-up", summary="Nope.", why_it_matters=""),
                    ],
                ),
                SimpleNamespace(
                    id="ai",
                    items=[SimpleNamespace(id="ai-1", summary="A real item.", why_it_matters="")],
                ),
            ],
        )
        digest = assemble(parsed, candidates, {"ai_items": 8, "canada_items": 6})
        self.assertEqual([section.id for section in digest.sections], ["ai", "canada"])
        self.assertEqual(digest.sections[0].items[0].title, "Real")
        self.assertEqual(len(digest.sections[1].items), 1)
        self.assertTrue(digest.sections[1].items[0].watchlist_hit)
        self.assertNotIn("made-up", render_telegram(digest, limit=5, archive_url=None))

    def test_keyword_word_boundaries(self) -> None:
        self.assertEqual(match_keywords("email about available seats", ["AI"]), [])
        self.assertEqual(match_keywords("Ontario AI policy", ["AI"]), ["AI"])
        self.assertEqual(match_keywords("ROCm support landed", ["ROCm", "Vulkan"]), ["ROCm"])


class DigestArchiveTests(unittest.TestCase):
    def test_saved_markdown_round_trips(self) -> None:
        original = Digest(
            subject="AI + Ontario briefing — October 6, 2026",
            intro="One development, and one Canadian note.",
            sections=[
                DigestSection(
                    id="ai",
                    title="AI",
                    items=[
                        DigestItem(
                            title='Mistral Large 4: "Le Chonk"',
                            url="https://mistral.ai/news/mistral-large-4/",
                            source="Hacker News (watchlist)",
                            summary="Weights are promised by the end of the month.",
                            why_it_matters="It would be one of the largest openly downloadable models.",
                            watchlist_hit=True,
                            discussion_url="https://news.ycombinator.com/item?id=49978116",
                        )
                    ],
                ),
                DigestSection(
                    id="canada",
                    title="Ontario and Canada",
                    items=[
                        DigestItem(
                            title="Ottawa launches a council",
                            url="https://www.cbc.ca/news/example",
                            source="Google News",
                            summary="Ottawa launched a council.",
                            why_it_matters="",
                            watchlist_hit=False,
                        )
                    ],
                ),
            ],
        )
        self.assertEqual(render_markdown(digest_from_markdown(render_markdown(original))), render_markdown(original))


if __name__ == "__main__":
    unittest.main()
