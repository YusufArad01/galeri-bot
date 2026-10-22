import unittest

from src.scraper.engine import split_proxy


class SplitProxyTests(unittest.TestCase):
    def test_with_credentials(self):
        self.assertEqual(split_proxy("http://kul:sifre@1.2.3.4:8080"),
                         {"server": "http://1.2.3.4:8080", "username": "kul", "password": "sifre"})

    def test_special_characters_in_password(self):
        r = split_proxy("http://kul:p%40ss%3Aw@proxy.example.com:3128")
        self.assertEqual((r["username"], r["password"], r["server"]), ("kul", "p@ss:w", "http://proxy.example.com:3128"))

    def test_without_credentials_or_scheme(self):
        self.assertEqual(split_proxy("http://1.2.3.4:8080"), {"server": "http://1.2.3.4:8080"})
        self.assertEqual(split_proxy("1.2.3.4:8080"), {"server": "http://1.2.3.4:8080"})

    def test_socks(self):
        self.assertEqual(split_proxy("socks5://u:p@h:1080")["server"], "socks5://h:1080")


if __name__ == "__main__":
    unittest.main()
