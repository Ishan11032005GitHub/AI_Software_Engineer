import requests

class GitHubClient:
    def __init__(self, token: str):
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        }

    def get(self, url):
        return requests.get(url, headers=self.headers)

    def post(self, url, json):
        return requests.post(url, headers=self.headers, json=json)
