# Xray / 3x-ui integration

**Merge these fragments into an existing configuration; they are not a complete Xray server config.** Warp-Manager does not edit 3x-ui, users, inbounds, certificates or routing. Existing default routing should remain under the operator's control.

## Generate from actual saved ports

```bash
warp-pool --instance 1 xray
warp-pool xray-routing --youtube-instance 2 --gemini-instance 1
```

[Outbound example](outbound.fragment.json) uses instance 1's default SOCKS 40010.
[Dual-mode routing](youtube-gemini.fragment.json) uses instance 2 YouTube 40012 and instance 1 Gemini 40010, plus a targeted UDP/443 block. Regenerate after changing ports. Preserve the existing default outbound first; append the exported outbounds and insert the service rules before broad catch-all rules.

The YouTube list covers youtube.com, youtu.be, googlevideo.com, ytimg.com and youtubei.googleapis.com. Gemini rules cover gemini.google.com, aistudio.google.com and generativelanguage.googleapis.com. They are service-routing examples, not a claim that every Google product/account endpoint is covered.

## Domains, sniffing and WARP DNS

Attach the [sniffing fragment](inbound-sniffing.fragment.json) to the **real traffic-receiving inbound**, or otherwise deliver destination domains to Xray. The example uses `routeOnly: false` so a sniffed name can replace the original destination and be forwarded to SOCKS for remote resolution.

With `routeOnly: true`, a sniffed name is used for matching while the original destination IP remains. That can make the client path differ from domain-based probes, including IPv4/IPv6 choice. Choose deliberately rather than treating the flag as a cosmetic setting. The behavior and supported sniffers are defined by the [official Xray inbound documentation](https://github.com/XTLS/Xray-docs-next/blob/main/docs/en/config/inbound.md).

Sniffing cannot always identify encrypted/hidden names such as ECH. Domain forwarding still does not pin a WARP IP or prove that an authenticated Google account has the same result as an anonymous probe. Verify the actual client and Xray access logs after integration.

## TCP / QUIC

The SOCKS front carries TCP and does not implement UDP ASSOCIATE. The combined example blocks UDP/443 **only for the selected service domains**, allowing clients that support it to fall back to TCP. It does not globally block every UDP/443 flow. Domain identification must work for those rules to match.

## Host and Docker networking

For 3x-ui/Xray on the VPS host, `127.0.0.1:40010/40012` reaches the local fronts.
Inside a Docker bridge container, 127.0.0.1 means that container itself. Use host networking or an intentional existing private IPv4 bind where allowed. YouTube remains loopback-only, so host networking is the straightforward Docker arrangement for that role. Do not publish an unauthenticated proxy on a public address.

## Проверка

```bash
warp-pool --all status
curl -4 -fsS --noproxy '' --proxy socks5h://127.0.0.1:40010 https://www.cloudflare.com/cdn-cgi/trace
curl -4 -fsS --noproxy '' --proxy http://127.0.0.1:40013 https://www.cloudflare.com/cdn-cgi/trace
```

`warp=on/plus` подтверждает WARP для запроса. Доступность Gemini отдельно смотрится в status и проверяется с реальным клиентом. Работающий systemd и успешный trace сами по себе не означают `SERVING`.
