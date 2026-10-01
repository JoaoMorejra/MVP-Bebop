# Benchmark de inferencia por device

- Data: 2026-09-30
- Estacao: Intel(R) Core(TM) i5-7200U CPU @ 2.50GHz; NVIDIA GeForce MX110 (torch 2.9.1+cu126); OpenVINO 2026.4.0-22959-99c81491cc3-releases/2026/4
- Quadro: bench_target.jpg (856x480)
- Gerado por `scripts/bench_inference_devices.py` (3 x 40 rodadas intercaladas); CPU = uso total da estacao durante a inferencia continua; mjpeg = FPS recebido do bridge em paralelo (`-` sem `--mjpeg-url`).

| device | imgsz | p50 ms | p95 ms | inferencias/s | CPU % | mjpeg FPS |
|---|---|---|---|---|---|---|
| CUDA FP32 | 320 | 47.1 | 48.6 | 21.2 | 30.5 | - |
| CUDA FP32 | 480 | 48.6 | 49.8 | 20.5 | 31.9 | - |
| CUDA FP32 | 640 | 63.6 | 64.8 | 15.7 | 31.3 | - |
| CUDA FP16 | 320 | 43.3 | 44.6 | 23.0 | 30.1 | - |
| CUDA FP16 | 480 | 45.0 | 46.4 | 22.1 | 30.5 | - |
| CUDA FP16 | 640 | 54.6 | 55.7 | 18.2 | 30.3 | - |
| IGPU FP16 | 320 | 18.4 | 22.1 | 52.7 | 58.4 | - |
| IGPU FP16 | 480 | 26.1 | 30.4 | 37.0 | 54.2 | - |
| IGPU FP16 | 640 | 37.4 | 41.2 | 26.4 | 51.1 | - |
| CPU | 320 | 19.1 | 22.2 | 52.8 | 75.9 | - |
| CPU | 480 | 38.5 | 46.5 | 25.7 | 67.4 | - |
| CPU | 640 | 64.8 | 75.8 | 15.1 | 63.2 | - |

- Menor p95 a 320 px: IGPU FP16 (22.1 ms).
- Menor p95 a 480 px: IGPU FP16 (30.4 ms).
- Menor p95 a 640 px: IGPU FP16 (41.2 ms).
