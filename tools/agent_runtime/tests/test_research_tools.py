from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


RUNTIME = Path(__file__).resolve().parents[1]
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from research_tools import ResearchClient, ResearchToolError  # noqa: E402


class ResearchToolTests(unittest.TestCase):
    def config(self) -> dict:
        return {
            "runtime_root": str(RUNTIME / "tests" / "runtime_test_data"),
            "web_research_enabled": True,
            "searxng_url": "http://127.0.0.1:8080",
            "crawl4ai_url": "http://127.0.0.1:11235",
            "stellaris_wiki_api_url": "https://stellaris.paradoxwikis.com/api.php",
            "web_search_allowed_domains": [
                "stellaris.paradoxwikis.com",
                "forum.paradoxplaza.com",
            ],
        }

    @patch("research_tools.socket.getaddrinfo")
    @patch("research_tools._json_request")
    def test_search_then_fetch_is_bounded_to_discovered_url(
        self,
        request_json,
        getaddrinfo,
    ) -> None:
        getaddrinfo.return_value = [
            (2, 1, 6, "", ("93.184.216.34", 443))
        ]
        request_json.side_effect = [
            {
                "results": [
                    {
                        "title": "Economy guide",
                        "url": "https://forum.paradoxplaza.com/example",
                        "content": "A community result.",
                    }
                ]
            },
            {"results": [{"markdown": {"raw_markdown": "Page body"}}]},
        ]
        client = ResearchClient(self.config())
        result = client.search_web({"query": "Stellaris economy"})
        self.assertEqual(result["count"], 1)
        page = client.fetch_page({"url": result["results"][0]["url"]})
        self.assertEqual(page["content"], "Page body")
        self.assertTrue(page["untrusted_reference"])

        with self.assertRaises(ResearchToolError):
            client.fetch_page(
                {"url": "https://forum.paradoxplaza.com/not-searched"}
            )

    @patch("research_tools.socket.getaddrinfo")
    @patch("research_tools._json_request")
    def test_wiki_search_returns_structured_untrusted_reference(
        self,
        request_json,
        getaddrinfo,
    ) -> None:
        getaddrinfo.return_value = [
            (2, 1, 6, "", ("93.184.216.34", 443))
        ]
        request_json.return_value = {
            "query": {
                "pages": [
                    {
                        "title": "Economy",
                        "fullurl": "https://stellaris.paradoxwikis.com/Economy",
                        "extract": "Community-maintained reference text.",
                    }
                ]
            }
        }
        client = ResearchClient(self.config())
        result = client.search_stellaris_wiki({"query": "economy"})
        self.assertEqual(result["count"], 1)
        self.assertEqual(
            result["results"][0]["source_type"],
            "community_reference_wiki",
        )

    @patch("research_tools.socket.getaddrinfo")
    @patch("research_tools._json_request")
    def test_wiki_search_falls_back_to_bounded_searxng_site_search(
        self,
        request_json,
        getaddrinfo,
    ) -> None:
        getaddrinfo.return_value = [
            (2, 1, 6, "", ("93.184.216.34", 443))
        ]
        request_json.side_effect = [
            ResearchToolError("远端没有返回有效 UTF-8 JSON。"),
            {
                "results": [
                    {
                        "title": "Economy",
                        "url": "https://stellaris.paradoxwikis.com/Economy",
                        "content": "Community-maintained reference text.",
                    }
                ]
            },
        ]
        client = ResearchClient(self.config())
        result = client.search_stellaris_wiki({"query": "economy"})
        self.assertEqual(result["source"], "searxng_site_fallback")
        self.assertEqual(result["count"], 1)
        self.assertEqual(
            result["results"][0]["source_type"],
            "community_reference_wiki",
        )
        self.assertIn(result["results"][0]["url"], client.discovered_urls)

    def test_disabled_client_rejects_research_without_network_access(self) -> None:
        config = self.config()
        config["web_research_enabled"] = False
        client = ResearchClient(config)
        with self.assertRaisesRegex(ResearchToolError, "玩家已关闭联网检索"):
            client.search_web({"query": "Stellaris economy"})


if __name__ == "__main__":
    unittest.main()
