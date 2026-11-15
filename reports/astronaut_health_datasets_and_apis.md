# NASA Astronaut Health Monitoring — Datasets, APIs, and Data Sources Guide
> **Day-0 research note (14 Nov 2026), kept for the record.** It was written before any code existed. The datasets it lists were a starting list; the data the app actually uses is listed in the [README](../README.md).


## 1. Executive Summary & Project Focus Identification
An inspection of the team workspace revealed research notes titled:
> **"Space Apps Astronaut Health Monitoring Plan"**

This confirms that the primary project focus is **Astronaut & Biomedical Health Monitoring** for space missions (specifically aligned with the **NASA Space Apps Challenge: "Create Health Monitoring Software for Astronauts on Space Missions"**).

### The Scientific Framework: NASA's 5 Hazards of Human Spaceflight
Astronaut health monitoring systems must address the **5 Hazards of Human Spaceflight** (Human Research Program - HRP):
1. **Space Radiation:** Galactic cosmic rays (GCR) and solar particle events (SPE) requiring continuous dosimetric tracking.
2. **Isolation & Confinement:** Disruption of circadian rhythm, psychological stress, depression, cognitive fatigue.
3. **Distance from Earth:** Communication delays (up to ~22 minutes one-way to Mars) necessitating **autonomous onboard clinical decision support** without ground control intervention.
4. **Altered Gravity Fields:** Fluid shifts, cardiovascular deconditioning, bone mineral density loss, muscle atrophy, Spaceflight Associated Neuro-ocular Syndrome (SANS).
5. **Hostile / Closed Environments:** Elevated cabin $\text{CO}_2$ levels (hypercapnia), altered oxygen partial pressure ($\text{ppO}_2$), acoustic noise, toxic trace contaminants.

---

## 2. Curated Datasets, APIs, and Telemetry Sources

### Category A: Official NASA Spaceflight Biology & Radiation Datasets

| Resource Name | Provider | Data Format | Key Metrics & Features | Direct URL |
| :--- | :--- | :--- | :--- | :--- |
| **NASA Open Science Data Repository (OSDR)** | NASA Ames / GeneLab / ALSDA | REST JSON API, CSV, TSV, AWS S3 | Spaceflight multi-omics, physiological telemetry, immune dysregulation, muscle atrophy, cardiovascular remodeling | [osdr.nasa.gov](https://osdr.nasa.gov/) |
| **SOMA (Space Omics & Medical Atlas)** | NASA OSDR / Weill Cornell / Nature | HDF5, CSV, FASTQ, Biobank Portal | Inspiration4 (I4) civilian astronaut vital telemetry, continuous multi-omics, microbiome, cytokine profiles | [SOMA Portal](https://osdr.nasa.gov/) / [Nature SOMA](https://www.nature.com/immersive/d42859-024-00009-8/index.html) |
| **NASA OSDR RadLab** | NASA Ames Research Center | REST API, CSV, Parquet | Real space radiation dosimetry from MSL Curiosity (RAD), Artemis-I BioSentinel, LRO CRaTER | [RadLab Dashboard](https://visualization.osdr.nasa.gov/radlab/) |
| **NASA Life Sciences Data Archive (LSDA)** | NASA Johnson Space Center | Tabular metadata, clinical summaries | Physiological, metabolic, and neurovestibular measurements across Apollo, Shuttle, and ISS missions | [lsda.jsc.nasa.gov](https://lsda.jsc.nasa.gov/) |

#### Quick Integration Guide (NASA OSDR REST API):
NASA OSDR exposes a public REST interface requiring no API key for standard queries:
```python
import requests

# Query dataset metadata for Inspiration4 civilian mission or spaceflight studies
url = "https://osdr.nasa.gov/osdr/data/osd/v2/dataset/OSD-48/"
response = requests.get(url)
data = response.json()
print("Dataset Title:", data['study']['title'])
print("Assays & Metrics:", list(data['study']['assays'].keys()))
```

---

### Category B: Clinical & Wearable Vital Telemetry (High-Resolution Physiological Data)

| Resource Name | Source / Institution | Data Format | Key Metrics & Features | Direct URL |
| :--- | :--- | :--- | :--- | :--- |
| **VitalDB Open Clinical Telemetry** | Seoul National University / Open Access | Python library, `.vital`, CSV, Web API | Multi-lead ECG (100–500 Hz), Arterial Blood Pressure (ART), PPG / Pulse oximetry ($\text{SpO}_2$), End-Tidal $\text{CO}_2$ ($\text{EtCO}_2$), Body Temp, Respiration | [vitaldb.net](https://vitaldb.net/) |
| **WESAD (Wearable Stress & Affect Detection)** | University of Siegen / UCI Repository | Python Pickle (`.pkl`), CSV (`.zip`) | Empatica E4 wristband & RespiBAN chest band: ECG, BVP, EDA (Galvanic Skin Response), Skin Temp, 3-axis Accelerometer, Respiration. Labeled for stress vs baseline | [UCI WESAD](https://archive.ics.uci.edu/dataset/465/wesad+wearable+stress+and+affect+detection) |
| **Sleep-EDF / CAP Sleep Database** | PhysioNet (MIT / Harvard) | European Data Format (`.edf`), Python `mne` | Multi-channel EEG, EOG, EMG, Respiration, sleep stages (REM, N1, N2, N3). Essential for circadian disruption tracking | [PhysioNet Sleep-EDF](https://physionet.org/content/sleep-edfx/1.0.0/) |
| **MIMIC Waveform Database Matched Subset** | PhysioNet | WFDB format, CSV | Continuous high-frequency ECG, arterial pressure, PPG, and respiratory waveforms with annotated arrhythmias | [PhysioNet MIMIC](https://physionet.org/content/mimic3wdb-matched/1.0/) |

#### Quick Integration Guide (VitalDB in 3 lines of Python):
`vitaldb` is completely open, requires zero API keys, and downloads directly into NumPy arrays:
```python
# pip install vitaldb pandas
import vitaldb
import pandas as pd

# 1. Fetch continuous ECG and Arterial Blood Pressure at 100 Hz
track_names = ['SNUADC/ECG_II', 'SNUADC/ART']
waveform_data = vitaldb.load_case(1, track_names, interval=1/100)
ecg = waveform_data[:, 0]
blood_pressure = waveform_data[:, 1]

# 2. Fetch vital numerics (Heart Rate, SpO2, EtCO2, Temp) at 1 Hz
numerics = vitaldb.load_case(1, ['Solar8000/HR', 'Solar8000/PLETH_SPO2', 'Solar8000/ETCO2', 'Solar8000/TEMP1'], interval=1.0)
df_vitals = pd.DataFrame(numerics, columns=['HR', 'SpO2', 'EtCO2', 'BodyTemp'])
```

---

### Category C: Space Habitat & ECLSS Environmental Telemetry

| Resource Name | Source / Technology | Data Format | Key Metrics & Features | Direct URL |
| :--- | :--- | :--- | :--- | :--- |
| **ISS Mimic Live Telemetry** | NASA Johnson Space Center (JSC) Open Source | Lightstreamer stream, WebSocket, JSON | Live International Space Station telemetry: Cabin $\text{ppO}_2$, Cabin $\text{ppCO}_2$, total atmospheric pressure, temperature, solar power | [GitHub ISS-Mimic](https://github.com/ISS-Mimic/Mimic) / [Live Dashboard](https://iss-mimic.github.io/Mimic/dashboard.html) |
| **NASA Prognostics Data Repository (PCoE)** | NASA Ames Research Center | Space-delimited `.txt`, MATLAB `.mat`, CSV | Li-ion battery aging (voltage, current, temperature degradation) and C-MAPSS engine/pump wear. Simulates Spacesuit PLSS / ECLSS pump life | [NASA PCoE](https://www.nasa.gov/intelligent-systems-division/discovery-and-systems-health/pcoe/pcoe-data-set-repository/) |

---

### Category D: Synthetic Physiological Stream Generation & Anomaly Simulation

For demo presentations, judges prioritize seeing **real-time alert states, dynamic thresholds, and anomaly injection**. Real astronauts rarely have recorded acute medical emergencies on public channels, so synthetic telemetry generation is the gold standard approach.

#### Simulation Engine: `NeuroKit2`
```bash
pip install neurokit2
```

```python
import neurokit2 as nk
import pandas as pd
import numpy as np

def generate_astronaut_telemetry(duration_sec=60, fs=100, scenario="nominal"):
    """
    Generates synchronized astronaut vital signs.
    Scenarios: 'nominal', 'hypoxia', 'eva_stress', 'hypercapnia'
    """
    if scenario == "nominal":
        hr, rr, spo2, stress_peaks = 70, 14, 99.0, 1
    elif scenario == "hypoxia":
        hr, rr, spo2, stress_peaks = 120, 26, 84.0, 4
    elif scenario == "eva_stress":
        hr, rr, spo2, stress_peaks = 135, 22, 97.0, 8
    elif scenario == "hypercapnia":  # High CO2 in spacesuit/capsule
        hr, rr, spo2, stress_peaks = 105, 28, 93.0, 5

    # Synthesize physiological waveforms
    ecg = nk.ecg_simulate(duration=duration_sec, sampling_rate=fs, heart_rate=hr)
    rsp = nk.rsp_simulate(duration=duration_sec, sampling_rate=fs, respiratory_rate=rr)
    eda = nk.eda_simulate(duration=duration_sec, sampling_rate=fs, scr_number=stress_peaks)
    
    # Add vital signs numerics
    timestamps = np.linspace(0, duration_sec, len(ecg))
    spo2_stream = np.random.normal(spo2, 0.2, len(ecg))
    
    return pd.DataFrame({
        "timestamp": timestamps,
        "ecg_waveform": ecg,
        "respiration_waveform": rsp,
        "eda_conductance": eda,
        "spo2": spo2_stream
    })
```

---

## 3. Recommended Software Architecture for the NASA Space Apps Demo

To impress hackathon evaluators, structure the project around three integrated layers:

```
+-----------------------------------------------------------------------------------+
|                            ASTRONAUT WEARABLES & SENSORS                         |
|  - Smartwatch / Biosensor Patch (ECG, PPG, HR, SpO2, Skin Temp, EDA)              |
|  - Spacesuit / Habitat Sensors (Cabin CO2, O2, Pressure, Ambient Radiation)      |
+-----------------------------------------+-----------------------------------------+
                                          |
                                          v (Streaming via WebSocket / MQTT / REST)
+-----------------------------------------------------------------------------------+
|                        ONBOARD EDGE MONITORING ENGINE (Python)                     |
|  1. Ingestion: Reads VitalDB / WESAD / NeuroKit2 synthetic telemetry stream        |
|  2. Anomaly Detection Engine: Threshold rules & ML models (Isolation Forest / SVM)  |
|     - Acute Stress Detector (EDA + HRV RMSSD calculation)                         |
|     - Hypoxia / Hypercapnia Warning (SpO2 < 90% or CO2 > 4.0 mmHg)                 |
|     - Arrhythmia Detector (QRS peak irregularity detection)                       |
|     - Radiation Cumulative Dose Counter (OSDR RadLab calibration)                 |
|  3. Autonomous Clinical Decision Support:                                         |
|     - Offline medical action protocols (NASA Flight Surgeon emergency checklist)  |
+-----------------------------------------+-----------------------------------------+
                                          |
                                          v (JSON WebSockets / SSE / REST)
+-----------------------------------------------------------------------------------+
|                    CREW MEDICAL OFFICER DASHBOARD (UI / Frontend)                  |
|  - Real-time Waveform Visualizer (ECG & Respiration rolling charts)               |
|  - 5 Hazards Risk Index (Radiation, Sleep/Fatigue, Vital Status, Capsule Air)     |
|  - Triage Alert Banner: Nominal (Green), Caution (Yellow), Critical (Red)          |
|  - One-click Emergency Countermeasure Checklist (Oxygen pre-breathe, calming drill)|
+-----------------------------------------------------------------------------------+
```

### Standard Telemetry JSON Schema for Dashboard Communication:
```json
{
  "mission_elapsed_time": "MET 042:14:22:05",
  "crew_member_id": "ASTRO-01",
  "crew_member_name": "Commander Sarah Connor",
  "vitals": {
    "heart_rate_bpm": 74.2,
    "blood_pressure_sys": 118,
    "blood_pressure_dia": 76,
    "spo2_percent": 98.4,
    "core_temp_celsius": 36.8,
    "respiration_rate_rpm": 14.1,
    "stress_index_gsr": 0.24,
    "sleep_quality_score": 88
  },
  "environment": {
    "cabin_pressure_kpa": 101.3,
    "pp_o2_kpa": 21.0,
    "pp_co2_mmhg": 2.1,
    "ambient_radiation_microsv_hr": 18.4,
    "cumulative_mission_dose_msv": 42.6
  },
  "status": {
    "overall_triage": "NOMINAL",
    "active_alarms": []
  }
}
```

---

## 4. Immediate Next Steps for the Team
1. **Choose Data Feed Mode:**
   - **Offline / ML Training:** Use **VitalDB** for cardiac waveforms and **WESAD** for stress/fatigue classification.
   - **Real-Time Interactive Pitch Demo:** Implement the **`NeuroKit2` + FastAPI WebSocket simulator** to demonstrate dynamic trigger states (switching between "Nominal Flight", "SpO2 Drop / Hypoxia", and "EVA Panic Stress").
2. **Anchor in NASA Context:**
   - Reference **NASA OSDR / SOMA (Inspiration4)** in project documentation and slides to validate scientific credibility.
   - Frame the user interface around NASA's **5 Hazards of Human Spaceflight**.
3. **Build the Dashboard:**
   - Implement multi-channel charts (ECG/PPG, vitals gauges, cabin environment status) with intuitive color thresholds.
