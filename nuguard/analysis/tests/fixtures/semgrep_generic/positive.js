const { exec, execSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const jwt = require('jsonwebtoken');
const serialize = require('node-serialize');

function search(req, res) {
  db.query(`SELECT * FROM products WHERE name = '${req.query.q}'`, (err, rows) => {
    res.send(rows);
  });
}

function run(req, res) {
  exec(req.query.cmd, (err, stdout) => {
    res.send(stdout);
  });
}

function readFile(req, res) {
  fs.readFile(path.join(__dirname, req.query.name), (err, data) => {
    res.send(data);
  });
}

function echo(req, res) {
  res.send(req.query.msg);
}

function goTo(req, res) {
  res.redirect(req.query.url);
}

function hashPassword(pw) {
  return crypto.createHash('md5').update(pw).digest('hex');
}

const token = jwt.sign({ id: 1 }, "supersecret123");

const apiKey = "sk-abcdefghijklmnopqrstuvwxyz";

function run2(req) {
  eval(req.query.expr);
}

function restoreComment(req) {
  const obj = serialize.unserialize(req.body.data);
  return obj;
}
