#!/usr/bin/env python

"""
Termux Network Tester
---------------------
Una aplicación web Flask para ejecutar pruebas de red (RSSI, latencia, iperf3)
en múltiples ubicaciones desde un dispositivo Android con Termux.

Dependencias de Termux:
  pkg install python iperf3 termux-api

Dependencias de Python:
  pip install flask

Ejecución:
  python run_tests.py --host 0.0.0.0 --port 5000
"""

import os
import subprocess
import json
import threading
import time
import argparse
import statistics
import re
import io
import csv
from flask import Flask, render_template_string, jsonify, request, send_file

# --- Configuración de la Aplicación Flask ---
app = Flask(__name__)

# --- Estado Global de la Aplicación ---
# (Usamos un diccionario para manejar el estado de forma mutable y segura entre hilos)
app_state = {
    "status": "idle",  # idle, running, paused, complete, stopped, error
    "current_location": "N/A",
    "current_iteration": 0,
    "total_iterations": 3,
    "iperf_host": "",
    "iperf_duration": 60,
    "results_log": [],      # Log detallado de cada iteración
    "summary_log": {},      # Resumen por ubicación
    "current_log_entry": "Esperando para iniciar...",
    "error_message": ""
}

# --- Eventos de Sincronización de Hilos ---
pause_event = threading.Event()
stop_event = threading.Event()
test_thread = None

# --- Plantilla HTML (Frontend) ---
# Se inyecta Tailwind CSS desde CDN.
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Termux Network Tester</title>
    <!-- Carga de Tailwind CSS -->
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        /* Estilos para el log */
        #log-output {
            font-family: 'Courier New', Courier, monospace;
            white-space: pre-wrap;
            word-break: break-all;
            max-height: 400px;
            overflow-y: auto;
        }
        /* Ocultar flechas en input[type=number] */
        input::-webkit-outer-spin-button,
        input::-webkit-inner-spin-button {
            -webkit-appearance: none;
            margin: 0;
        }
        input[type=number] {
            -moz-appearance: textfield;
        }
        /* Estilos para pestañas de resultados */
        .tab-button {
            @apply inline-block text-gray-400 hover:text-cyan-400 hover:border-cyan-400 border-b-2 border-transparent py-2 px-4 font-medium transition duration-200;
        }
        .tab-button.active {
            @apply text-cyan-400 border-cyan-400;
        }
        .location-tab-button {
            @apply inline-block bg-gray-700 hover:bg-gray-600 text-gray-300 py-1 px-3 rounded-md text-sm transition duration-200 cursor-pointer;
        }
        .location-tab-button.active {
            @apply bg-cyan-600 text-white;
        }
    </style>
</head>
<body class="bg-gray-900 text-gray-200 font-sans antialiased">

    <div class="container max-w-4xl mx-auto p-4 md:p-8 space-y-6">

        <header class="text-center">
            <h1 class="text-3xl font-bold text-cyan-400">Termux Network Tester</h1>
            <p class="text-lg text-gray-400">Medidor de RSSI, Latencia y Throughput</p>
        </header>

        <!-- Sección de Controles -->
        <section id="controls" class="bg-gray-800 p-6 rounded-lg shadow-lg space-y-4">
            <h2 class="text-xl font-semibold border-b border-gray-700 pb-2">Configuración</h2>
            
            <div>
                <label for="iperf-host" class="block text-sm font-medium text-gray-300 mb-1">Servidor iperf3 (Host/IP)</label>
                <input type="text" id="iperf-host" class="w-full bg-gray-700 border border-gray-600 rounded-md px-3 py-2 text-white placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-cyan-500" placeholder="ej: iperf.example.com">
            </div>

            <div class="grid grid-cols-2 gap-4">
                <div>
                    <label for="iterations" class="block text-sm font-medium text-gray-300 mb-1">Iteraciones (1-5)</label>
                    <input type="number" id="iterations" value="3" min="1" max="5" class="w-full bg-gray-700 border border-gray-600 rounded-md px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-cyan-500">
                </div>
                <div>
                    <label for="duration" class="block text-sm font-medium text-gray-300 mb-1">Duración iperf3 (s)</label>
                    <input type="number" id="duration" value="60" min="5" class="w-full bg-gray-700 border border-gray-600 rounded-md px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-cyan-500">
                </div>
            </div>

            <!-- Botones de Acción (Diseño de Grid) -->
            <div class="grid grid-cols-2 gap-4 pt-2">
                <button id="btn-start" class="col-span-2 bg-green-600 hover:bg-green-700 text-white font-bold py-2 px-4 rounded-md transition duration-200 shadow-md">
                    Iniciar Pruebas
                </button>
                <button id="btn-resume" class="col-span-1 bg-cyan-600 hover:bg-cyan-700 text-white font-bold py-2 px-4 rounded-md transition duration-200 shadow-md hidden">
                    Reanudar (Siguiente Ubicación)
                </button>
                <button id="btn-stop" class="col-span-1 bg-red-600 hover:bg-red-700 text-white font-bold py-2 px-4 rounded-md transition duration-200 shadow-md hidden">
                    Detener Pruebas
                </button>
            </div>
        </section>

        <!-- Sección de Estado -->
        <section id="status" class="bg-gray-800 p-6 rounded-lg shadow-lg">
            <h2 class="text-xl font-semibold border-b border-gray-700 pb-2 mb-4">Estado Actual</h2>
            <div class="grid grid-cols-1 sm:grid-cols-3 gap-4 text-center">
                <div>
                    <span class="block text-sm text-gray-400">Estado</span>
                    <span id="status-text" class="text-2xl font-bold text-yellow-400">Idle</span>
                </div>
                <div>
                    <span class="block text-sm text-gray-400">Ubicación</span>
                    <span id="status-location" class="text-2xl font-bold">N/A</span>
                </div>
                <div>
                    <span class="block text-sm text-gray-400">Iteración</span>
                    <span id="status-iteration" class="text-2xl font-bold">N/A</span>
                </div>
            </div>
            <div id="status-log" class="mt-4 bg-gray-900 rounded-md p-3 text-sm text-gray-300">
                <p id="log-entry">Esperando para iniciar...</p>
            </div>
            <div id="error-message" class="mt-4 bg-red-900 border border-red-700 text-red-200 p-3 rounded-md hidden"></div>
        </section>

        <!-- Sección de Resultados -->
        <section id="results" class="bg-gray-800 p-6 rounded-lg shadow-lg">
            <div class="flex flex-col sm:flex-row justify-between sm:items-center border-b border-gray-700 pb-2 mb-4 gap-4">
                <h2 class="text-xl font-semibold">Resultados</h2>
                <div id="download-buttons" class="hidden space-x-2">
                    <a id="btn-download-json" href="/download/json" class="bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium py-2 px-3 rounded-md transition duration-200">
                        JSON (Todo)
                    </a>
                    <a id="btn-download-csv" href="/download/csv" class="bg-teal-600 hover:bg-teal-700 text-white text-sm font-medium py-2 px-3 rounded-md transition duration-200">
                        CSV (Iteraciones)
                    </a>
                </div>
            </div>

            <!-- Pestañas de Navegación de Resultados -->
            <div class="mb-4">
                <div class="flex flex-wrap -mb-px" id="results-tabs" aria-label="Tabs">
                    <button class="tab-button active" data-tab="summary">Resumen Total</button>
                    <button class="tab-button" data-tab="details">Detalle (Iteraciones)</button>
                </div>
            </div>
            
            <!-- Contenedor de Pestañas de Ubicación -->
            <div id="location-tabs-container" class="mb-4 hidden">
                <p class="text-sm text-gray-400 mb-2">Filtrar por ubicación (clic para [des]activar):</p>
                <div class="flex flex-wrap gap-2" id="location-tabs">
                    <!-- Botones de ubicación (p1, p2...) se generan dinámicamente -->
                </div>
            </div>

            <pre id="summary-output" class="bg-gray-900 rounded-md p-4 text-sm overflow-x-auto">Esperando resultados...</pre>
        </section>

    </div>

    <script>
        // Funciones auxiliares
        const $ = (id) => document.getElementById(id);
        const show = (id) => $(id).classList.remove('hidden');
        const hide = (id) => $(id).classList.add('hidden');

        let pollInterval;
        let currentView = 'summary'; // 'summary' o 'details'
        let selectedLocation = null; // null (para todos) o 'p1', 'p2', etc.
        let appData = {}; // Cache para los datos de /status

        // --- Lógica del Frontend ---

        /** Renderiza la sección de resultados basado en el estado actual */
        function renderResults() {
            const summaryLog = appData.summary_log || {};
            const resultsLog = appData.results_log || [];
            const outputElement = $('summary-output');
            
            // Actualizar pestañas principales (Resumen/Detalle)
            document.querySelectorAll('#results-tabs .tab-button').forEach(btn => {
                btn.classList.toggle('active', btn.dataset.tab === currentView);
            });

            // Generar y actualizar pestañas de ubicación (p1, p2...)
            const locationTabsContainer = $('location-tabs');
            const locationTabsContainerWrapper = $('location-tabs-container');
            const locations = Object.keys(summaryLog);

            if (locations.length > 0) {
                locationTabsContainer.innerHTML = ''; // Limpiar pestañas anteriores
                locations.sort().forEach(loc => {
                    const btn = document.createElement('button');
                    btn.className = 'location-tab-button';
                    btn.textContent = loc.toUpperCase();
                    btn.dataset.location = loc;
                    btn.classList.toggle('active', loc === selectedLocation);
                    locationTabsContainer.appendChild(btn);
                });
                show('location-tabs-container');
            } else {
                hide('location-tabs-container');
            }

            // Renderizar contenido en el <pre>
            let content = {};
            if (currentView === 'summary') {
                if (selectedLocation) {
                    content = summaryLog[selectedLocation] || { "error": "No hay datos para esta ubicación." };
                } else {
                    content = summaryLog; // Resumen total
                }
            } else if (currentView === 'details') {
                if (selectedLocation) {
                    content = resultsLog.filter(r => r.location === selectedLocation);
                } else {
                    content = resultsLog; // Todas las iteraciones
                }
                if (content.length === 0) content = { "info": "No hay iteraciones para mostrar." };
            }

            if (Object.keys(content).length === 0 && resultsLog.length === 0) {
                    outputElement.textContent = "Esperando resultados...";
            } else {
                outputElement.textContent = JSON.stringify(content, null, 2);
            }
        }

        /** Actualiza la UI basado en el estado del backend */
        async function updateStatus() {
            try {
                const response = await fetch('/status');
                if (!response.ok) {
                    throw new Error('Error de conexión con el servidor');
                }
                const data = await response.json();
                appData = data; // Cachear la respuesta completa

                // Actualizar textos de estado
                $('status-text').textContent = data.status.charAt(0).toUpperCase() + data.status.slice(1);
                $('status-location').textContent = data.current_location || 'N/A';
                $('status-iteration').textContent = data.current_iteration ? `${data.current_iteration}/${data.total_iterations}` : 'N/A';
                $('log-entry').textContent = data.current_log_entry;

                // Actualizar colores de estado
                const statusText = $('status-text');
                statusText.classList.remove('text-yellow-400', 'text-green-400', 'text-cyan-400', 'text-red-400');
                if (data.status === 'running') statusText.classList.add('text-green-400');
                else if (data.status === 'paused') statusText.classList.add('text-cyan-400');
                else if (data.status === 'complete') statusText.classList.add('text-green-400');
                else if (data.status === 'stopped' || data.status === 'error') statusText.classList.add('text-red-400');
                else statusText.classList.add('text-yellow-400');

                // Mostrar/ocultar mensaje de error
                if (data.status === 'error' && data.error_message) {
                    $('error-message').textContent = data.error_message;
                    show('error-message');
                } else {
                    hide('error-message');
                }

                // Actualizar botones de acción (grid layout)
                const btnStart = $('btn-start');
                const btnResume = $('btn-resume');
                const btnStop = $('btn-stop');

                // Reset spans for grid layout
                btnStart.classList.remove('col-span-1', 'col-span-2');
                btnResume.classList.remove('col-span-1', 'col-span-2');
                btnStop.classList.remove('col-span-1', 'col-span-2');

                if (data.status === 'running') {
                    hide('btn-start');
                    hide('btn-resume');
                    show('btn-stop');
                    btnStop.classList.add('col-span-2'); // Stop button takes full width

                    $('iperf-host').disabled = true;
                    $('iterations').disabled = true;
                    $('duration').disabled = true;
                } else if (data.status === 'paused') {
                    hide('btn-start');
                    show('btn-resume');
                    show('btn-stop');
                    btnResume.classList.add('col-span-1'); // Resume is half
                    btnStop.classList.add('col-span-1');   // Stop is half
                    
                    // Asegurarse que los inputs sigan deshabilitados
                    $('iperf-host').disabled = true;
                    $('iterations').disabled = true;
                    $('duration').disabled = true;
                } else { // idle, complete, stopped, error
                    show('btn-start');
                    hide('btn-resume');
                    hide('btn-stop');
                    btnStart.classList.add('col-span-2'); // Start button takes full width

                    $('iperf-host').disabled = false;
                    $('iterations').disabled = false;
                    $('duration').disabled = false;
                    if (pollInterval) {
                        clearInterval(pollInterval); // Detener polling
                        pollInterval = null;
                    }
                }

                // Actualizar resumen llamando a la nueva función
                renderResults();

                // Botones de descarga
                if (data.status === 'complete' || data.status === 'stopped') {
                    if (data.results_log && data.results_log.length > 0) {
                        show('download-buttons');
                    }
                } else {
                    hide('download-buttons');
                }

            } catch (error) {
                $('status-text').textContent = 'Error';
                $('status-text').classList.add('text-red-400');
                $('log-entry').textContent = `Error de conexión: ${error.message}. ¿Está el servidor Python corriendo?`;
                if (pollInterval) {
                    clearInterval(pollInterval);
                    pollInterval = null;
                }
            }
        }

        /** Iniciar las pruebas */
        $('btn-start').addEventListener('click', async () => {
            const host = $('iperf-host').value;
            const iterations = parseInt($('iterations').value, 10);
            const duration = parseInt($('duration').value, 10);

            if (!host) {
                // No usamos alert()
                $('error-message').textContent = 'Por favor, introduce el host del servidor iperf3.';
                show('error-message');
                return;
            }

            if (isNaN(iterations) || iterations < 1 || iterations > 5) {
                $('error-message').textContent = 'Las iteraciones deben ser un número entre 1 y 5.';
                show('error-message');
                return;
            }

            if (isNaN(duration) || duration < 5) {
                $('error-message').textContent = 'La duración debe ser de al menos 5 segundos.';
                show('error-message');
                return;
            }
            
            hide('error-message');
            
            // Resetear vista
            currentView = 'summary';
            selectedLocation = null;
            appData = {};
            renderResults(); // Limpia la vista de resultados anterior
            $('summary-output').textContent = "Iniciando pruebas...";

            try {
                const response = await fetch('/start', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ host, iterations, duration })
                });
                const data = await response.json();
                
                if (data.status === 'started') {
                    if (!pollInterval) {
                        pollInterval = setInterval(updateStatus, 1000); // Iniciar polling
                    }
                    updateStatus(); // Actualización inmediata
                } else {
                    $('log-entry').textContent = `Error al iniciar: ${data.error || 'Error desconocido'}`;
                    $('status-text').textContent = 'Error';
                }
            } catch (error) {
                 $('log-entry').textContent = `Error al iniciar: ${error.message}`;
            }
        });

        /** Reanudar las pruebas */
        $('btn-resume').addEventListener('click', async () => {
            await fetch('/resume', { method: 'POST' });
            if (!pollInterval) {
                pollInterval = setInterval(updateStatus, 1000); // Reiniciar polling si se detuvo
            }
            updateStatus();
        });

        /** Detener las pruebas */
        $('btn-stop').addEventListener('click', async () => {
            // Reemplazamos confirm() con una acción directa
            // if (confirm('¿Estás seguro de que quieres detener las pruebas?')) {
            await fetch('/stop', { method: 'POST' });
            updateStatus();
            // }
        });

        // Carga inicial
        updateStatus();

        // --- Event Listeners para Pestañas de Resultados ---
        $('results-tabs').addEventListener('click', (e) => {
            if (e.target.classList.contains('tab-button')) {
                const tab = e.target.dataset.tab;
                currentView = tab;
                selectedLocation = null; // Resetear ubicación al cambiar de pestaña principal
                renderResults();
            }
        });

        $('location-tabs').addEventListener('click', (e) => {
                if (e.target.classList.contains('location-tab-button')) {
                const location = e.target.dataset.location;
                // Alternar selección: si ya estaba seleccionado, ponerlo en null (mostrar todo)
                selectedLocation = (selectedLocation === location) ? null : location;
                renderResults();
            }
        });
    </script>

</body>
</html>
"""

# --- Funciones de Pruebas (Backend) ---

def set_state(key, value):
# ... (el resto del código Python no necesita cambios) ...
# ... (existing code ... )
    """Actualiza una clave en el estado global."""
# ... (existing code ... )
    app_state[key] = value

def log_status(message):
# ... (existing code ... )
# ... (el resto del código Python no necesita cambios) ...
# ... (existing code ... )
    """Actualiza el mensaje de log actual."""
# ... (existing code ... )
    print(message) # Log a consola
    set_state("current_log_entry", message)

def safe_float(value, default=None):
# ... (existing code ... )
    """Convierte a float de forma segura."""
# ... (existing code ... )
    try:
        return float(value)
# ... (existing code ... )
    except (ValueError, TypeError):
        return default

def p95(data):
# ... (existing code ... )
    """Calcula el percentil 95 de una lista de números."""
# ... (existing code ... )
    if not data:
        return None
# ... (existing code ... )
    sorted_data = sorted(data)
    index = int(len(sorted_data) * 0.95)
# ... (existing code ... )
    # Asegurarse de que el índice esté dentro de los límites
    index = min(index, len(sorted_data) - 1)
# ... (existing code ... )
    return sorted_data[index]

def get_rssi():
# ... (existing code ... )
    """Obtiene el RSSI usando termux-api."""
# ... (existing code ... )
    try:
        cmd = ["termux-wifi-connectioninfo"]
# ... (existing code ... )
        process = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=True)
        data = json.loads(process.stdout)
# ... (existing code ... )
        rssi = data.get("rssi")
        return int(rssi) if rssi is not None else None
# ... (existing code ... )
    except FileNotFoundError:
        log_status("Error: termux-api no encontrado. ¿Está instalado?")
# ... (existing code ... )
        set_state("error_message", "termux-api no encontrado. Instala termux-api.")
    except subprocess.TimeoutExpired:
# ... (existing code ... )
        log_status("Error: Timeout al obtener RSSI.")
    except subprocess.CalledProcessError as e:
# ... (existing code ... )
        log_status(f"Error al ejecutar termux-wifi-connectioninfo: {e.stderr}")
    except json.JSONDecodeError:
# ... (existing code ... )
        log_status("Error: No se pudo decodificar la salida de termux-api.")
    except Exception as e:
# ... (existing code ... )
        log_status(f"Error inesperado en get_rssi: {e}")
    return None

def run_ping(host, count=10):
# ... (existing code ... )
    """Ejecuta ping y calcula latencia media y jitter."""
# ... (existing code ... )
    latencies = []
    jitters = []
# ... (existing code ... )
    avg_latency = None
    avg_jitter = None
# ... (existing code ... )
    
    try:
# ... (existing code ... )
        cmd = ["ping", "-c", str(count), "-i", "0.2", host] # -i 0.2 para pings más rápidos
        process = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
# ... (existing code ... )
        
        # Extraer latencias usando regex
# ... (existing code ... )
        latencies = [safe_float(t) for t in re.findall(r"time=([\d\.]+)\s*ms", process.stdout)]
        latencies = [t for t in latencies if t is not None]

        if latencies:
# ... (existing code ... )
            avg_latency = statistics.mean(latencies)
            # Calcular jitter como la variación entre pings consecutivos
# ... (existing code ... )
            if len(latencies) > 1:
                jitters = [abs(latencies[i+1] - latencies[i]) for i in range(len(latencies)-1)]
# ... (existing code ... )
                if jitters:
                    avg_jitter = statistics.mean(jitters)
# ... (existing code ... )
    
    except subprocess.TimeoutExpired:
# ... (existing code ... )
        log_status(f"Error: Timeout en ping a {host}")
    except FileNotFoundError:
# ... (existing code ... )
        log_status("Error: Comando 'ping' no encontrado.")
        set_state("error_message", "Comando 'ping' no encontrado.")
# ... (existing code ... )
    except Exception as e:
        log_status(f"Error inesperado en run_ping: {e}")
# ... (existing code ... )
        
    return avg_latency, avg_jitter, latencies

def run_iperf(host, duration, reverse=False):
# ... (existing code ... )
    """Ejecuta iperf3 y devuelve la tasa de bits y el JSON crudo."""
# ... (existing code ... )
    bits_per_second = None
    raw_json = {}
# ... (existing code ... )
    direction = "Download" if reverse else "Upload"
    
    try:
# ... (existing code ... )
        cmd = ["iperf3", "-c", host, "-t", str(duration), "--json"]
        if reverse:
# ... (existing code ... )
            cmd.append("-R") # Modo inverso (Download)
        
        process = subprocess.run(cmd, capture_output=True, text=True, timeout=duration + 10)
# ... (existing code ... )
        raw_json = json.loads(process.stdout)

        if "error" in raw_json:
# ... (existing code ... )
            log_status(f"Error de iperf3 ({direction}): {raw_json['error']}")
            return None, raw_json
# ... (existing code ... )
        
        # 'sum_received' para Download, 'sum_sent' para Upload
# ... (existing code ... )
        if reverse: # Download
            bits_per_second = raw_json.get("end", {}).get("sum_received", {}).get("bits_per_second")
# ... (existing code ... )
        else: # Upload
            bits_per_second = raw_json.get("end", {}).get("sum_sent", {}).get("bits_per_second")

        return safe_float(bits_per_second), raw_json

# ... (existing code ... )
    except subprocess.TimeoutExpired:
        log_status(f"Error: Timeout en iperf3 {direction} a {host}")
# ... (existing code ... )
    except FileNotFoundError:
        log_status("Error: 'iperf3' no encontrado. ¿Está instalado?")
# ... (existing code ... )
        set_state("error_message", "iperf3 no encontrado. Instala iperf3.")
        set_state("status", "error")
# ... (existing code ... )
    except json.JSONDecodeError:
        log_status(f"Error: No se pudo decodificar la salida JSON de iperf3 ({direction}).")
# ... (existing code ... )
        if process.stdout:
            log_status(f"Salida iperf3: {process.stdout[:200]}...")
# ... (existing code ... )
    except Exception as e:
        log_status(f"Error inesperado en run_iperf ({direction}): {e}")
# ... (existing code ... )
        
    return None, raw_json

def calculate_summary(location_results):
# ... (existing code ... )
    """Calcula estadísticas de resumen para una ubicación."""
# ... (existing code ... )
    summary = {}
    metrics = {
# ... (existing code ... )
        "rssi": [r["rssi"] for r in location_results if r["rssi"] is not None],
        "latency": [r["latency"] for r in location_results if r["latency"] is not None],
# ... (existing code ... )
        "jitter": [r["jitter"] for r in location_results if r["jitter"] is not None],
        "download_mbps": [r["download_bps"] / 1_000_000 for r in location_results if r["download_bps"] is not None],
# ... (existing code ... )
        "upload_mbps": [r["upload_bps"] / 1_000_000 for r in location_results if r["upload_bps"] is not None]
    }

    for key, data in metrics.items():
# ... (existing code ... )
        if data:
            summary[f"{key}_mean"] = round(statistics.mean(data), 2)
# ... (existing code ... )
            summary[f"{key}_median"] = round(statistics.median(data), 2)
            summary[f"{key}_p95"] = round(p95(data), 2)
# ... (existing code ... )
            summary[f"{key}_min"] = round(min(data), 2)
            summary[f"{key}_max"] = round(max(data), 2)
# ... (existing code ... )
            summary[f"{key}_samples"] = len(data)
        else:
# ... (existing code ... )
            summary[f"{key}_mean"] = None
            summary[f"{key}_median"] = None
# ... (existing code ... )
            summary[f"{key}_p95"] = None
            summary[f"{key}_min"] = None
# ... (existing code ... )
            summary[f"{key}_max"] = None
            summary[f"{key}_samples"] = 0
# ... (existing code ... )
            
    return summary

def test_runner_thread():
# ... (existing code ... )
    """
    El hilo principal que ejecuta el ciclo de pruebas.
    """
# ... (existing code ... )
    try:
        # Obtener configuración del estado global
# ... (existing code ... )
        host = app_state["iperf_host"]
        iterations = app_state["total_iterations"]
# ... (existing code ... )
        duration = app_state["iperf_duration"]
        
        locations = [f"p{i}" for i in range(1, 9)] # p1..p8
# ... (existing code ... )
        
        set_state("status", "running")
# ... (existing code ... )
        set_state("error_message", "")
        
        for loc in locations:
# ... (existing code ... )
            if stop_event.is_set():
                log_status(f"Pruebas detenidas por el usuario en {loc}.")
# ... (existing code ... )
                break
            
            set_state("current_location", loc)
# ... (existing code ... )
            location_results = []
            
            for i in range(1, iterations + 1):
# ... (existing code ... )
                if stop_event.is_set():
                    log_status(f"Pruebas detenidas por el usuario en {loc}, iteración {i}.")
# ... (existing code ... )
                    break
                
                set_state("current_iteration", i)
# ... (existing code ... )
                log_status(f"Ubicación {loc}, Iteración {i}/{iterations}: Iniciando...")
                
                iteration_data = {
# ... (existing code ... )
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "location": loc,
# ... (existing code ... )
                    "iteration": i
                }
# ... (existing code ... )
                
                # 1. Medir RSSI
                log_status(f"({loc}-{i}) Obteniendo RSSI...")
# ... (existing code ... )
                iteration_data["rssi"] = get_rssi()
                if stop_event.is_set(): break
# ... (existing code ... )
                
                # 2. Medir Latencia y Jitter (Ping)
                log_status(f"({loc}-{i}) Ejecutando ping a {host}...")
# ... (existing code ... )
                lat, jit, lat_raw = run_ping(host)
                iteration_data["latency"] = lat
# ... (existing code ... )
                iteration_data["jitter"] = jit
                iteration_data["ping_raw"] = lat_raw
# ... (existing code ... )
                if stop_event.is_set(): break
                
                # 3. Medir Upload (iperf3)
# ... (existing code ... )
                log_status(f"({loc}-{i}) Ejecutando iperf3 Upload (dur: {duration}s)...")
                up_bps, up_raw = run_iperf(host, duration, reverse=False)
# ... (existing code ... )
                iteration_data["upload_bps"] = up_bps
                iteration_data["iperf_upload_raw"] = up_raw
# ... (existing code ... )
                if stop_event.is_set(): break

                # 4. Medir Download (iperf3 -R)
# ... (existing code ... )
                log_status(f"({loc}-{i}) Ejecutando iperf3 Download (dur: {duration}s)...")
                down_bps, down_raw = run_iperf(host, duration, reverse=True)
# ... (existing code ... )
                iteration_data["download_bps"] = down_bps
                iteration_data["iperf_download_raw"] = down_raw
# ... (existing code ... )
                
                app_state["results_log"].append(iteration_data)
                location_results.append(iteration_data)
# ... (existing code ... )
                log_status(f"({loc}-{i}) Completada. (DL: {down_bps/1_000_000:.2f} Mbps, UL: {up_bps/1_000_000:.2f} Mbps, Lat: {lat:.2f} ms)")

            # Calcular resumen para la ubicación
# ... (existing code ... )
            if location_results:
                app_state["summary_log"][loc] = calculate_summary(location_results)

            # Pausar si no es la última ubicación
# ... (existing code ... )
            if loc != locations[-1] and not stop_event.is_set():
                log_status(f"Completada la ubicación {loc}. Pausando. Muévete a la siguiente ubicación y presiona 'Reanudar'.")
# ... (existing code ... )
                set_state("status", "paused")
                pause_event.clear()
# ... (existing code ... )
                
                # Esperar a que se presione 'Reanudar' (pause_event.set()) o 'Detener'
                pause_event.wait()
# ... (existing code ... )
                
                if stop_event.is_set():
# ... (existing code ... )
                    log_status(f"Pruebas detenidas durante la pausa en {loc}.")
                    break
# ... (existing code ... )
                
                set_state("status", "running")

        # Fin del bucle
# ... (existing code ... )
        if stop_event.is_set():
            set_state("status", "stopped")
# ... (existing code ... )
            log_status("Pruebas detenidas.")
        else:
# ... (existing code ... )
            set_state("status", "complete")
            log_status("Todas las pruebas han sido completadas.")

    except Exception as e:
# ... (existing code ... )
        log_status(f"Error fatal en el hilo de pruebas: {e}")
        set_state("status", "error")
# ... (existing code ... )
        set_state("error_message", str(e))
    finally:
# ... (existing code ... )
        # Limpieza
        set_state("current_location", "N/A")
# ... (existing code ... )
        set_state("current_iteration", 0)


# --- Rutas de la API de Flask ---

@app.route('/')
def index():
# ... (existing code ... )
    """Sirve la página principal."""
    return render_template_string(HTML_TEMPLATE)

@app.route('/start', methods=['POST'])
def start_test():
# ... (existing code ... )
    """Inicia un nuevo ciclo de pruebas."""
    global test_thread
# ... (existing code ... )
    if app_state["status"] == "running" or app_state["status"] == "paused":
        return jsonify({"status": "error", "error": "Pruebas ya en ejecución"}), 400

    data = request.json
# ... (existing code ... )
    
    # Resetear estado
    app_state["iperf_host"] = data.get("host")
# ... (existing code ... )
    app_state["total_iterations"] = int(data.get("iterations", 3))
    app_state["iperf_duration"] = int(data.get("duration", 60))
# ... (existing code ... )
    app_state["results_log"] = []
    app_state["summary_log"] = {}
# ... (existing code ... )
    app_state["error_message"] = ""
    app_state["current_log_entry"] = "Iniciando..."
# ... (existing code ... )
    
    stop_event.clear()
    pause_event.clear()

    # Iniciar hilo de pruebas
# ... (existing code ... )
    test_thread = threading.Thread(target=test_runner_thread)
    test_thread.daemon = True # El hilo morirá si la app principal muere
# ... (existing code ... )
    test_thread.start()
    
    return jsonify({"status": "started"})

@app.route('/resume', methods=['POST'])
def resume_test():
# ... (existing code ... )
    """Reanuda las pruebas si están en pausa."""
    if app_state["status"] == "paused":
# ... (existing code ... )
        pause_event.set()
        return jsonify({"status": "resumed"})
# ... (existing code ... )
    return jsonify({"status": "not_paused"}), 400

@app.route('/stop', methods=['POST'])
def stop_test():
# ... (existing code ... )
    """Detiene las pruebas en ejecución o en pausa."""
    if app_state["status"] == "running" or app_state["status"] == "paused":
# ... (existing code ... )
        stop_event.set()
        if app_state["status"] == "paused":
# ... (existing code ... )
            pause_event.set() # Desbloquear el hilo si está en pausa
        
        # Esperar un poco a que el hilo termine
# ... (existing code ... )
        if test_thread:
            test_thread.join(timeout=2.0)
# ... (existing code ... )
            
        set_state("status", "stopped")
# ... (existing code ... )
        log_status("Pruebas detenidas por el usuario.")
        return jsonify({"status": "stopped"})
# ... (existing code ... )
    return jsonify({"status": "not_running"}), 400

@app.route('/status')
def get_status():
# ... (existing code ... )
    """Devuelve el estado actual de la aplicación."""
    # Devuelve una copia del estado
# ... (existing code ... )
    return jsonify(app_state.copy())

@app.route('/download/json')
def download_json():
# ... (existing code ... )
    """Envía los resultados completos como un archivo JSON."""
    data_to_export = {
# ... (existing code ... )
        "summary": app_state["summary_log"],
        "details": app_state["results_log"]
# ... (existing code ... )
    }
    
    # Crear un archivo en memoria
# ... (existing code ... )
    f = io.BytesIO()
    f.write(json.dumps(data_to_export, indent=2).encode('utf-8'))
# ... (existing code ... )
    f.seek(0)
    
    filename = f"network_test_results_{time.strftime('%Y%m%d_%H%M%S')}.json"
# ... (existing code ... )
    
    return send_file(
        f,
# ... (existing code ... )
        mimetype='application/json',
        as_attachment=True,
# ... (existing code ... )
        download_name=filename
    )

@app.route('/download/csv')
def download_csv():
# ... (existing code ... )
    """Envía los resultados detallados como un archivo CSV."""
    if not app_state["results_log"]:
# ... (existing code ... )
        return "No hay datos para exportar", 404

    # Crear archivo CSV en memoria
# ... (existing code ... )
    f = io.StringIO()
    writer = csv.writer(f)
# ... (existing code ... )
    
    # Escribir cabeceras
    # Tomar las claves de la primera fila como cabeceras, excluyendo las crudas
# ... (existing code ... )
    headers = [key for key in app_state["results_log"][0].keys() if not key.endswith('_raw')]
    writer.writerow(headers)
# ... (existing code ... )
    
    # Escribir filas
    for row in app_state["results_log"]:
# ... (existing code ... )
        writer.writerow([row.get(h) for h in headers])
        
    # Rebobinar y enviar
# ... (existing code ... )
    f_bytes = io.BytesIO(f.getvalue().encode('utf-8'))
    f_bytes.seek(0)
# ... (existing code ... )
    
    filename = f"network_test_results_{time.strftime('%Y%m%d_%H%M%S')}.csv"

    return send_file(
# ... (existing code ... )
        f_bytes,
        mimetype='text/csv',
# ... (existing code ... )
        as_attachment=True,
        download_name=filename
# ... (existing code ... )
    )

# --- Punto de Entrada ---
if __name__ == '__main__':
# ... (existing code ... )
    parser = argparse.ArgumentParser(description="Servidor web Termux Network Tester")
    parser.add-argument('--host', type=str, default='0.0.0.0',
# ... (existing code ... )
                        help='Host en el que escuchar (default: 0.0.0.0)')
    parser.add-argument('--port', type=int, default=5000,
# ... (existing code ... )
                        help='Puerto en el que escuchar (default: 5000)')
    args = parser.parse-args()

    print(f"*** Iniciando Termux Network Tester en http://{args.host}:{args.port} ***")
# ... (existing code ... )
    print("Abre http://localhost:5000 en el navegador de tu teléfono.")
    
    # Usar 'threaded=True' es importante para que el polling de la UI
# ... (existing code ... )
    # y el hilo de pruebas no se bloqueen mutuamente.
    app.run(host=args.host, port=args.port, threaded=True)

