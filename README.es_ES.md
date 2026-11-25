![Warp-Manager](docs/assets/banner.svg)

# Warp-Manager

[English](README.md) · [فارسی](README.fa_IR.md) · [العربية](README.ar_EG.md) · [中文](README.zh_CN.md) · [Español](README.es_ES.md) · [Русский](README.ru_RU.md) · [Türkçe](README.tr_TR.md)

Gestor de túneles Cloudflare WARP para servidores Linux: políticas independientes para Gemini y YouTube, proxy local SOCKS5/HTTP, supervisión por túnel y escalado por capacidad con reservas activas.

**0.4.1**

## Requisitos e instalación

Se necesita root, Ubuntu/Debian x86_64, systemd, /dev/net/tun, Internet y Python 3.10+. Se probó Ubuntu 24.04 con Python 3.12. El instalador añade Docker/Compose y herramientas que falten y conserva Docker existente. El cliente oficial WARP se descarga durante la compilación y se inicia con --accept-tos.

Desde el archivo completo o una copia del repositorio:

```bash
cd Warp-Manager
sudo bash install.sh
```

Después de publicar Forcus356/Warp-Manager en la rama main, también estará disponible este instalador independiente:

```bash
curl -fsSL https://raw.githubusercontent.com/Forcus356/Warp-Manager/main/install_warp_manager.sh -o /tmp/install_warp_manager.sh
sudo bash /tmp/install_warp_manager.sh
```

El bootstrap resuelve la referencia a un commit inmutable, descarga el árbol completo por HTTPS y muestra commit y SHA-256 antes de instalar. Desde una copia local se utiliza install.sh. No detecta automáticamente el GitHub Release más reciente.

## Consola y modos

La orden instalada es warp-pool; se mantienen las rutas/servicios warp-google-pool por compatibilidad. La consola utiliza etiquetas rusas; los indicadores CLI son iguales en todos los idiomas. Sin argumentos, install.sh abre el menú en un terminal. t abre plantillas; Easy/Advanced edita un borrador con vista previa y cancelación.

Gemini exige SERVING de una prueba anónima de generación. REFUSED, CAPTCHA e INCONCLUSIVE no son éxito. El resultado puede diferir para una cuenta autenticada.

YouTube exige la región observada de Google RU y desactiva Gemini en esa instancia separada. RU VERIFIED no garantiza ausencia de anuncios ni calidad de vídeo.

```bash
sudo bash install.sh --instance 1 --role gemini --target-gbps 1 --tunnels 12 --impersonate
sudo bash install.sh --instance 2 --role youtube --target-gbps 1 --tunnels 12
```

## Capacidad y reservas

target-gbps usa Gbit/s: 0.1 equivale a 100 Mbit/s; 0 selecciona un grupo fijo. Primero se mide cada túnel y después el grupo principal simultáneamente. La suma de pruebas individuales no confirma la velocidad común. Hay hasta 99 instancias y 1–128 ranuras por instancia; son límites de configuración, no capacidad garantizada del VPS.

Las reservas son max(1, floor(N/3)): 10 principales + 3 reservas; 30 + 10. Se verifican por separado y aceptan conexiones nuevas solo al ascender. Después se repone la reserva. El balanceador pondera conexiones activas/pendientes por velocidad medida. Una conexión TCP permanece en un solo túnel. Las ranuras inactivas no arrancan contenedores.

WARP no ofrece selección de país ni PIN de un servidor concreto. Las listas de países filtran lo observado por Google. La supervisión, el aislamiento de salidas rechazadas, el backoff y los límites de rotación son individuales.

## Operaciones

```bash
warp-pool
warp-pool --all status
warp-pool --instance 1 check all --full
warp-pool --instance 1 speed all
warp-pool --instance 1 logs -n 100
warp-pool --instance 1 capacity --gbps 1 --max-tunnels 12
warp-pool --instance 1 capacity --gbps 0 --max-tunnels 3
```

check/search/speed/rotate encolan solicitudes; código 0 significa aceptada, no prueba superada. Consulte status/logs. --all solo sirve para status/logs/config/xray/update. --interval del instalador y el menú usa minutos; los tiempos JSON usan segundos.

## Xray / 3x-ui

La ruta es Xray → SOCKS5 local → WARP → Internet. El exportador no modifica el panel. Conserve el outbound predeterminado primero y añada las reglas específicas antes de las generales. Los ejemplos explican host/Docker, dominios, DNS remoto y bloqueo UDP/443 limitado a los servicios elegidos. No hay UDP ASSOCIATE.

```bash
warp-pool xray-routing --youtube-instance 2 --gemini-instance 1
```

[Xray JSON + sniffing](docs/xray/README.md)

## Actualización y eliminación

```bash
warp-pool --instance 1 update /root/Warp-Manager
warp-pool --all update /root/Warp-Manager-v0.4.1.zip --sha256 "$EXPECTED_SHA256"
warp-pool --instance 2 uninstall
```

Las actualizaciones conservan configuración, puertos y volúmenes de registro. Los ZIP HTTPS requieren un SHA-256 esperado. Al eliminar una instancia, los perfiles locales se conservan salvo --purge-profiles. --purge-deps solo elimina dependencias propias que ya no tengan consumidores.

## Pruebas y límites medidos

En un VPS de 1 vCPU/2 GiB, ocho WARP reales superaron pruebas de 8/32/64 clientes sin errores. Con ocho clientes, 1/2/4/8 túneles entregaron aproximadamente 85/132/165/172 Mbit/s. CPU llegó al 100% y el generador TLS compartía esa CPU. No se verificó un gigabit ni 128 clientes reales sin errores. Se observaron dos resultados Gemini SERVING; no se estableció un grupo estable con reservas. La recuperación YouTube se verificó con objetivo de control de 100 Mbit/s.

```bash
python3 scripts/check_project.py
python3 -m unittest discover -s tests -v
python3 scripts/build_release.py
```

[Test methodology and results](docs/testing/RESULTS.md)

## Documentación y licencia

Las referencias detalladas están en inglés/ruso. El proyecto no incluye direcciones de VPS, credenciales SSH ni perfiles de registro reales. Las vistas de consola son demostraciones sintéticas, no evidencia de salidas verificadas.

[Documentation](docs/README.md) · [Configuration](docs/CONFIGURATION.md) · [Commands](docs/COMMANDS.md) · [Operations](docs/OPERATIONS.md) · [FAQ](docs/FAQ.md) · [Publishing](docs/PUBLISHING.md) · [Security](SECURITY.md)

Gestor no oficial bajo GPL-3.0-only. El cliente Cloudflare, descargado por separado, conserva sus propios términos.

[LICENSE](LICENSE) · [Attribution](THIRD_PARTY_NOTICES.md) · [Upstream notices](UPSTREAM_NOTICES.md)
