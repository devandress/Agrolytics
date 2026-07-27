# Open-Source Radar (SAR) Satellites for AgroVision

## Why radar?

AgroVision's primary feed is **Sentinel-2 optical** imagery. Optical sensors measure
reflected sunlight, so they are blind through clouds and at night — which is why the
ingestion code had to relax its cloud filter to 50% and widen the lookback window to
90 days (`app/services/satellite_ingestion.py`).

**Synthetic Aperture Radar (SAR)** emits its own microwave signal and measures the
backscatter, so it works **day or night, in any weather, through clouds**. For
agriculture it provides a continuous structural/moisture signal that complements
optical vegetation indices and fills the gaps when no clear optical scene exists.

## Open-source / free SAR sources compared

| Source | Operator | Band | Resolution | Access | Status in AgroVision |
|---|---|---|---|---|---|
| **Sentinel-1 RTC / GRD** | ESA Copernicus | C-band | ~10–20 m | **Free**, analysis-ready via Microsoft Planetary Computer STAC (`sentinel-1-rtc`), AWS Open Data, Copernicus Data Space | **Integrated** (`app/services/radar_ingestion.py`) |
| **SAOCOM-1A / 1B** | CONAE (Argentina) | L-band | ~10–100 m | Free for research; **CONAE registration**, no simple public STAC | Documented; L-band penetrates canopy/soil — strong fit for LATAM (deploy region São Paulo) |
| **ALOS-2 PALSAR-2 mosaics** | JAXA | L-band | 25 m (annual mosaic) | Free annual mosaics via AWS Open Data / Digital Earth Africa STAC | Documented; good for coarse seasonal/structural baselines |
| **NISAR** | NASA + ISRO | L + S-band | ~3–10 m | Free; emerging STAC access (NASA MAAP) | Future — newest high-res open SAR mission |
| **ROSE-L** | ESA Copernicus | L-band | TBD | Free (planned, ~2028) | Future |

## Chosen approach: Sentinel-1 RTC via Planetary Computer

Sentinel-1 RTC is the only option that drops directly into AgroVision's existing
stack (`pystac_client` + `planetary_computer.sign_inplace`), needs no extra
credentials, and ships **analysis-ready** (radiometrically terrain corrected,
tiled) data — so we avoid running our own SAR processing chain.

### Indices computed

From the dual-polarisation backscatter assets `vv` (co-pol) and `vh` (cross-pol),
both in **linear power**:

- **RVI — Radar Vegetation Index** = `4 * VH / (VV + VH)`
  All-weather proxy for vegetation density. Low (~0) over bare soil / water, higher
  over dense canopy. Complements optical NDVI.
- **VH/VV ratio** = `VH / VV`
  Sensitive to canopy structure and biomass; rises as vegetation develops.

Both formulas live in `app/services/indices.py` (`rvi`, `vh_vv_ratio`) and are
persisted as `Index` rows with `index_type` `RVI` / `VHVV` (added in migration `002`).

### Pipeline

- Service: `app/services/radar_ingestion.py` — mirrors the optical ingestion
  (polygon clip, reprojection, COG output under `data/cog/<field_id>/`).
- Task: `app/tasks/radar_tasks.run_radar_ingestion`.
- Schedule: every 6 h, offset +3 h from the optical job (`app/tasks/celery_app.py`).

### Notes / limitations

- RTC backscatter is **linear power**, not decibels. RVI/ratio are computed on
  linear values; convert to dB only for display if desired (`10 * log10(x)`).
- Speckle is inherent to SAR; field-mean values average most of it out. Add
  multitemporal or Lee speckle filtering later if per-pixel maps look noisy.
- L-band (SAOCOM/ALOS-2/NISAR) penetrates canopy and topsoil better than Sentinel-1's
  C-band — worth adding for soil-moisture work once CONAE/JAXA access is provisioned.

## Sources

- [Sentinel-1 RTC — Planetary Computer](https://planetarycomputer.microsoft.com/dataset/sentinel-1-rtc)
- [Sentinel-1 GRD — Planetary Computer](https://planetarycomputer.microsoft.com/dataset/sentinel-1-grd)
- [Sentinel-1 — AWS Registry of Open Data](https://registry.opendata.aws/sentinel-1/)
- [Analysis-Ready Sentinel-1 Backscatter — AWS Open Data](https://registry.opendata.aws/sentinel-1-rtc-indigo/)
- [OPERA RTC-S1 — NASA Earthdata](https://www.earthdata.nasa.gov/data/catalog/asf-opera-l2-rtc-s1-v1-1)
- [SAOCOM mission — eoPortal](https://www.eoportal.org/satellite-missions/saocom)
- [SAOCOM data products — ESA Earth Online](https://earth.esa.int/eogateway/catalog/saocom-data-products)
- [ALOS/JERS PALSAR — Digital Earth Africa / AWS Open Data](https://registry.opendata.aws/deafrica-alos-jers/)
- [PALSAR-2 ScanSAR CARD4L — AWS Open Data](https://registry.opendata.aws/jaxa-alos-palsar2-scansar/)
- [Simulated NISAR access — NASA MAAP docs](https://docs.maap-project.org/en/ogc/science/NISAR/Simulated_NISAR.html)
