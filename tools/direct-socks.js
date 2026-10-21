"use strict";

// Dependency-free SOCKS5 dispatcher for SecondNet.
// The listener is loopback-only. Every remote TCP/UDP socket and DNS query is
// explicitly bound to the IPv4 address of the adapter selected in the GUI.

const dgram = require("node:dgram");
const dns = require("node:dns");
const net = require("node:net");

function parseArguments(argv) {
  const result = new Map();
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key || !key.startsWith("--") || value === undefined) {
      throw new Error(`Invalid argument near ${key || "end of command"}`);
    }
    result.set(key.slice(2), value);
  }
  return result;
}

const options = parseArguments(process.argv.slice(2));
const listenHost = options.get("listen-host") || "127.0.0.1";
const listenPort = Number(options.get("listen-port"));
const bindAddress = options.get("bind-address") || "";
const dnsServers = (options.get("dns-servers") || "")
  .split(",")
  .map((value) => value.trim())
  .filter((value) => net.isIP(value) === 4);

if (!Number.isInteger(listenPort) || listenPort < 1 || listenPort > 65535) {
  throw new Error("--listen-port must be between 1 and 65535");
}
if (net.isIP(bindAddress) !== 4) {
  throw new Error("--bind-address must be an IPv4 address assigned to the selected adapter");
}

process.title = "SecondNet Direct SOCKS5";

const resolver = new dns.Resolver();
if (dnsServers.length > 0) {
  resolver.setServers(dnsServers);
}
resolver.setLocalAddress(bindAddress);

function resolveIPv4(host) {
  if (net.isIP(host) === 4) {
    return Promise.resolve(host);
  }
  if (net.isIP(host) === 6) {
    return Promise.reject(Object.assign(new Error("IPv6 destinations are not supported"), { code: "EAFNOSUPPORT" }));
  }
  return new Promise((resolve, reject) => {
    resolver.resolve4(host, (error, addresses) => {
      if (error) {
        reject(error);
      } else if (!addresses || addresses.length === 0) {
        reject(Object.assign(new Error(`No IPv4 address found for ${host}`), { code: "ENOTFOUND" }));
      } else {
        resolve(addresses[0]);
      }
    });
  });
}

function ipv4Bytes(address) {
  const parts = address.split(".").map(Number);
  if (parts.length !== 4 || parts.some((part) => !Number.isInteger(part) || part < 0 || part > 255)) {
    return [0, 0, 0, 0];
  }
  return parts;
}

function reply(code, address = "0.0.0.0", port = 0) {
  return Buffer.from([5, code, 0, 1, ...ipv4Bytes(address), (port >> 8) & 0xff, port & 0xff]);
}

function encodedEndpoint(address, port) {
  return Buffer.from([1, ...ipv4Bytes(address), (port >> 8) & 0xff, port & 0xff]);
}

function parseEndpoint(buffer, offset) {
  if (buffer.length <= offset) return null;
  const type = buffer[offset];
  let host;
  let next;
  if (type === 1) {
    if (buffer.length < offset + 7) return null;
    host = Array.from(buffer.subarray(offset + 1, offset + 5)).join(".");
    next = offset + 5;
  } else if (type === 3) {
    if (buffer.length < offset + 2) return null;
    const length = buffer[offset + 1];
    if (buffer.length < offset + 2 + length + 2) return null;
    host = buffer.subarray(offset + 2, offset + 2 + length).toString("utf8");
    next = offset + 2 + length;
  } else if (type === 4) {
    const error = new Error("IPv6 destinations are not supported by the selected IPv4 interface dispatcher");
    error.socksReply = 8;
    throw error;
  } else {
    const error = new Error(`Unsupported SOCKS5 address type ${type}`);
    error.socksReply = 8;
    throw error;
  }
  if (buffer.length < next + 2) return null;
  return {
    host,
    port: buffer.readUInt16BE(next),
    next: next + 2,
  };
}

function errorReplyCode(error) {
  if (error && Number.isInteger(error.socksReply)) return error.socksReply;
  switch (error && error.code) {
    case "ENETUNREACH": return 3;
    case "EHOSTUNREACH":
    case "ENOTFOUND":
    case "ETIMEDOUT": return 4;
    case "ECONNREFUSED": return 5;
    case "EAFNOSUPPORT": return 8;
    default: return 1;
  }
}

const udpAssociations = new Set();

function closeAssociation(association) {
  if (!association || association.closed) return;
  association.closed = true;
  udpAssociations.delete(association);
  try { association.local.close(); } catch (_) { /* already closed */ }
  try { association.remote.close(); } catch (_) { /* already closed */ }
}

async function createUdpAssociation(control) {
  const local = dgram.createSocket("udp4");
  const remote = dgram.createSocket("udp4");
  const association = { local, remote, client: null, closed: false };
  udpAssociations.add(association);

  local.on("message", async (message, remoteInfo) => {
    if (association.closed || message.length < 10 || message[0] !== 0 || message[1] !== 0 || message[2] !== 0) {
      return;
    }
    if (remoteInfo.address !== "127.0.0.1") return;
    if (association.client &&
        (association.client.address !== remoteInfo.address || association.client.port !== remoteInfo.port)) {
      return;
    }
    association.client = { address: remoteInfo.address, port: remoteInfo.port };
    try {
      const target = parseEndpoint(message, 3);
      if (!target) return;
      const address = await resolveIPv4(target.host);
      remote.send(message.subarray(target.next), target.port, address);
    } catch (error) {
      process.stdout.write(`UDP destination error: ${error.message}\n`);
    }
  });

  remote.on("message", (message, remoteInfo) => {
    if (association.closed || !association.client || net.isIP(remoteInfo.address) !== 4) return;
    const header = Buffer.concat([
      Buffer.from([0, 0, 0]),
      encodedEndpoint(remoteInfo.address, remoteInfo.port),
    ]);
    local.send(Buffer.concat([header, message]), association.client.port, association.client.address);
  });

  const fail = (error) => {
    if (!association.closed && control.writable) {
      control.end(reply(errorReplyCode(error)));
    }
    closeAssociation(association);
  };
  local.once("error", fail);
  remote.once("error", fail);

  await new Promise((resolve, reject) => {
    local.once("error", reject);
    local.bind(0, listenHost, resolve);
  });
  await new Promise((resolve, reject) => {
    remote.once("error", reject);
    remote.bind(0, bindAddress, resolve);
  });

  const localAddress = local.address();
  control.write(reply(0, listenHost, localAddress.port));
  const cleanup = () => closeAssociation(association);
  control.once("close", cleanup);
  control.once("error", cleanup);
}

async function connectTcp(client, target, initialData) {
  let responded = false;
  try {
    const address = await resolveIPv4(target.host);
    if (client.destroyed) return;
    const upstream = net.createConnection({
      host: address,
      port: target.port,
      localAddress: bindAddress,
      family: 4,
    });
    upstream.setNoDelay(true);
    upstream.setTimeout(30000, () => upstream.destroy(Object.assign(new Error("Connection timed out"), { code: "ETIMEDOUT" })));
    upstream.once("connect", () => {
      responded = true;
      upstream.setTimeout(0);
      const local = upstream.address();
      client.write(reply(0, typeof local === "object" ? local.address : bindAddress,
        typeof local === "object" ? local.port : 0));
      if (initialData.length > 0) upstream.write(initialData);
      client.pipe(upstream);
      upstream.pipe(client);
    });
    upstream.once("error", (error) => {
      if (!responded && client.writable) client.end(reply(errorReplyCode(error)));
      else client.destroy();
    });
    client.once("error", () => upstream.destroy());
    client.once("close", () => upstream.destroy());
  } catch (error) {
    if (client.writable) client.end(reply(errorReplyCode(error)));
  }
}

function handleClient(client) {
  client.setNoDelay(true);
  let state = "greeting";
  let buffered = Buffer.alloc(0);

  const reject = (code) => {
    if (client.writable) client.end(reply(code));
  };

  const consume = (chunk) => {
    buffered = Buffer.concat([buffered, chunk]);
    try {
      while (true) {
        if (state === "greeting") {
          if (buffered.length < 2) return;
          const methodCount = buffered[1];
          if (buffered.length < 2 + methodCount) return;
          if (buffered[0] !== 5 || !buffered.subarray(2, 2 + methodCount).includes(0)) {
            client.end(Buffer.from([5, 0xff]));
            return;
          }
          buffered = buffered.subarray(2 + methodCount);
          client.write(Buffer.from([5, 0]));
          state = "request";
          continue;
        }
        if (state === "request") {
          if (buffered.length < 4) return;
          if (buffered[0] !== 5) {
            reject(1);
            return;
          }
          const target = parseEndpoint(buffered, 3);
          if (!target) return;
          const command = buffered[1];
          const remaining = buffered.subarray(target.next);
          buffered = Buffer.alloc(0);
          client.removeListener("data", consume);
          if (command === 1) {
            void connectTcp(client, target, remaining);
          } else if (command === 3) {
            void createUdpAssociation(client).catch((error) => reject(errorReplyCode(error)));
          } else {
            reject(7);
          }
          return;
        }
        return;
      }
    } catch (error) {
      reject(errorReplyCode(error));
    }
  };

  client.on("data", consume);
  client.on("error", () => { /* handled by close/associated upstream */ });
}

const server = net.createServer(handleClient);
server.on("error", (error) => {
  process.stderr.write(`Direct SOCKS5 fatal error: ${error.message}\n`);
  process.exitCode = 1;
});
server.listen({ host: listenHost, port: listenPort, exclusive: true }, () => {
  process.stdout.write(
    `READY Direct SOCKS5 ${listenHost}:${listenPort} via ${bindAddress}` +
    `${dnsServers.length ? ` DNS ${dnsServers.join(",")}` : ""}\n`,
  );
});

function shutdown() {
  for (const association of udpAssociations) closeAssociation(association);
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(0), 1500).unref();
}

process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);