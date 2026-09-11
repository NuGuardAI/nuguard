const { exec, execSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const jwt = require('jsonwebtoken');

function search(req, res) {
  db.query("SELECT * FROM products WHERE name = ?", [req.query.q], (err, rows) => {
    res.send(rows);
  });
}

function run(req, res) {
  exec("ls -la", (err, stdout) => {
    res.send(stdout);
  });
}

function readFile(req, res) {
  const safePath = path.resolve(path.join(__dirname, req.query.name));
  if (!safePath.startsWith(path.resolve(__dirname))) {
    return res.status(400).end();
  }
  fs.readFile(safePath, (err, data) => {
    res.send(data);
  });
}

function echo(req, res) {
  res.send(escapeHtml(req.query.msg));
}

function goTo(req, res) {
  const allowed = new Set(["/home", "/about"]);
  const dest = req.query.url;
  res.redirect(allowed.has(dest) ? dest : "/home");
}

function hashPassword(pw) {
  return crypto.createHash('sha256').update(pw).digest('hex');
}

const token = jwt.sign({ id: 1 }, process.env.JWT_SECRET);

const apiKey = process.env.OPENAI_API_KEY;

function run2() {
  eval("1 + 1");
}
