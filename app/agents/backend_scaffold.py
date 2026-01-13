from __future__ import annotations

import os
import json
from typing import Dict, Any, List


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _write_json(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def scaffold_node_backend(repo_path: str, prompt: str) -> Dict[str, Any]:
    """
    Creates backend / Node + Express that:
    - serves the frontend statically from repo root
    - exposes minimal commerce APIs
    - persists orders/cart in backend/data/*.json
    """

    backend_dir = os.path.join(repo_path, "backend")
    data_dir = os.path.join(backend_dir, "data")

    os.makedirs(data_dir, exist_ok=True)

    # ---------------- Seed data ----------------

    products_path = os.path.join(data_dir, "products.json")
    if not os.path.exists(products_path):
        _write_json(
            products_path,
            [
                {"id": 1, "name": "Classic Tee", "price": 799, "inStock": True},
                {"id": 2, "name": "Denim Jacket", "price": 2499, "inStock": True},
                {"id": 3, "name": "Sneakers", "price": 1999, "inStock": True},
            ],
        )

    cart_path = os.path.join(data_dir, "cart.json")
    orders_path = os.path.join(data_dir, "orders.json")

    if not os.path.exists(cart_path):
        _write_json(cart_path, {"items": []})

    if not os.path.exists(orders_path):
        _write_json(orders_path, {"orders": []})

    # ---------------- package.json ----------------

    package_json = """{
  "name": "fashionstore-backend",
  "version": "1.0.0",
  "private": true,
  "main": "server.js",
  "type": "commonjs",
  "scripts": {
    "start": "node server.js"
  },
  "dependencies": {
    "cors": "^2.8.5",
    "express": "^4.19.2"
  }
}
"""

    # ---------------- server.js ----------------

    server_js = r"""const path = require("path");
const fs = require("fs");
const express = require("express");
const cors = require("cors");

const app = express();

app.use(cors());
app.use(express.json({ limit: "1mb" }));

const repoRoot = path.resolve(__dirname, "..");
const dataDir = path.resolve(__dirname, "data");

const productsFile = path.join(dataDir, "products.json");
const cartFile = path.join(dataDir, "cart.json");
const ordersFile = path.join(dataDir, "orders.json");

function readJson(file, fallback) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf-8"));
  } catch {
    return fallback;
  }
}

function writeJson(file, obj) {
  fs.writeFileSync(file, JSON.stringify(obj, null, 2));
}

app.get("/api/health", (req, res) => res.json({ ok: true }));

app.get("/api/products", (req, res) => {
  res.json(readJson(productsFile, []));
});

app.get("/api/cart", (req, res) => {
  res.json(readJson(cartFile, { items: [] }));
});

app.post("/api/cart", (req, res) => {
  const cart = readJson(cartFile, { items: [] });
  const item = req.body;

  if (!item || !item.id) {
    return res.status(400).json({ error: "Invalid item" });
  }

  cart.items.push(item);
  writeJson(cartFile, cart);
  res.json(cart);
});

app.post("/api/order", (req, res) => {
  const orders = readJson(ordersFile, { orders: [] });
  const order = {
    id: Date.now(),
    createdAt: new Date().toISOString(),
    ...req.body,
  };

  orders.orders.push(order);
  writeJson(ordersFile, orders);
  writeJson(cartFile, { items: [] });

  res.json(order);
});

app.use(express.static(repoRoot));

app.get("*", (req, res) => {
  const index = ["index.html", "public/index.html"]
    .map(p => path.join(repoRoot, p))
    .find(p => fs.existsSync(p));

  if (index) return res.sendFile(index);
  res.status(404).send("No index.html found");
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`Backend running at http://localhost:${PORT}`);
});
"""

    # ---------------- README ----------------

    readme = f"""# Backend (AutoTriage Generated)

Prompt:
{prompt}

## Run locally

```bash
cd backend
npm install
npm start
Frontend:
http://localhost:3000

Health:
GET /api/health

Products:
GET /api/products

Cart:
GET /api/cart
POST /api/cart

Orders:
POST /api/order
"""
    _write(os.path.join(backend_dir, "package.json"), package_json)
    _write(os.path.join(backend_dir, "server.js"), server_js)
    _write(os.path.join(backend_dir, "README.md"), readme)
    changed_files: List[str] = [
        "backend/package.json",
        "backend/server.js",
        "backend/README.md",
        "backend/data/products.json",
        "backend/data/cart.json",
        "backend/data/orders.json",
    ]

    return {
        "status": "backend_scaffolded",
        "backend_dir": "backend",
        "changed_files": changed_files,
    }
