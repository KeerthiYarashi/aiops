from random import choice

from locust import HttpUser, between, task


class ItemUser(HttpUser):
    wait_time = between(0.5, 2)

    def on_start(self):
        self.item_ids = []
        self.create_item()

    @task(2)
    def create_item(self):
        with self.client.post("/items", json={"name": "Locust demo item"}, catch_response=True) as response:
            if response.status_code == 201:
                item_id = response.json().get("id")
                if item_id:
                    self.item_ids = (self.item_ids + [item_id])[-50:]
                else:
                    response.failure("Create response did not contain an item ID")
            else:
                response.failure(f"Create returned HTTP {response.status_code}")

    @task(8)
    def read_item(self):
        if self.item_ids:
            self.client.get(f"/items/{choice(self.item_ids)}", name="/items/{item_id}")
