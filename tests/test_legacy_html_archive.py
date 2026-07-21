import unittest

from bs4 import BeautifulSoup

from pathlib import Path
from tempfile import TemporaryDirectory

from legacy_html_archive import (
    Capture,
    parse_modern_dom,
    parse_old_dom,
    render_v2_html,
)


class LegacyParserTests(unittest.TestCase):
    def setUp(self):
        self.capture = Capture(
            timestamp="20211119080126",
            original="https://twitter.com/zouzoudamowang/status/1461605464764915715",
            mimetype="text/html",
            statuscode="200",
            digest="digest",
            length="100",
            tweet_id="1461605464764915715",
        )

    def test_old_dom(self):
        soup = BeautifulSoup(
            """
            <div data-tweet-id="1461605464764915715"
                 data-screen-name="zouzoudamowang" data-name="Author"
                 data-user-id="123" data-conversation-id="1461605464764915715">
              <img class="js-action-profile-avatar"
                   src="https://pbs.twimg.com/profile_images/999/a.jpg">
              <span data-time-ms="1637308872000"></span>
              <p class="tweet-text">hello <b>world</b></p>
              <div data-image-url="https://pbs.twimg.com/media/ABC.jpg"></div>
            </div>
            """,
            "html.parser",
        )
        parsed = parse_old_dom(soup, self.capture, "zouzoudamowang")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.text, "hello world")
        self.assertEqual(parsed.author_username, "zouzoudamowang")
        self.assertEqual(parsed.images, ["https://pbs.twimg.com/media/ABC.jpg"])

    def test_modern_dom(self):
        soup = BeautifulSoup(
            """
            <div itemscope itemtype="https://schema.org/SocialMediaPosting">
              <meta itemprop="identifier" content="1461605464764915715">
              <meta itemprop="datePublished" content="2021-11-19T08:01:12.000Z">
              <div itemprop="author">
                <meta itemprop="identifier" content="123">
                <meta itemprop="additionalName" content="zouzoudamowang">
                <meta itemprop="givenName" content="Author">
              </div>
              <article data-testid="tweet">
                <img src="https://pbs.twimg.com/profile_images/999/a.jpg">
                <div data-testid="tweetText">modern tweet</div>
                <img src="https://pbs.twimg.com/media/XYZ.png">
              </article>
            </div>
            """,
            "html.parser",
        )
        parsed = parse_modern_dom(soup, self.capture, "zouzoudamowang")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.text, "modern tweet")
        self.assertEqual(parsed.author_name, "Author")
        self.assertEqual(parsed.images, ["https://pbs.twimg.com/media/XYZ.png"])

    def test_render_v2_html(self):
        value = {
            "data": {
                "id": "123",
                "text": "line one\nline two",
                "created_at": "2021-01-01T00:00:00Z",
                "attachments": {"media_keys": ["m1"]},
            },
            "includes": {
                "users": [{"name": "Author", "username": "author"}],
                "media": [
                    {
                        "media_key": "m1",
                        "type": "photo",
                        "url": "https://pbs.twimg.com/media/ABC.jpg",
                    }
                ],
            },
        }
        with TemporaryDirectory() as tmp:
            rendered = render_v2_html(value, Path(tmp), "../avatar/avatar.png")
        self.assertIn("line one<br/>", rendered)
        self.assertIn("tweet-image", rendered)
        self.assertIn("../avatar/avatar.png", rendered)


if __name__ == "__main__":
    unittest.main()
