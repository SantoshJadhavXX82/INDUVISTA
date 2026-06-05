#!/usr/bin/env python3
"""
build_fuelgas_reports.py  — create the 4 Mariner Pulq fuel-gas demo reports
(Current / Daily / Hourly / Chromatograph Current Status) in InduVista,
laid out to match the Daniel/Emerson report pages, fed by the two simulated
flow computers FUEL_GAS_FC001 (FC A) and FUEL_GAS_FC002 (FC B).

Host-run, stdlib only. Idempotent: find-or-create by report name, re-bind tags,
re-render a live preview to <name>.preview.html next to this script.

Usage (PowerShell):
    $env:SMOKE_BASE = "http://127.0.0.1:8000"      # optional, this is the default
    $env:SMOKE_USER = "admin"
    $sec = Read-Host "admin password" -AsSecureString
    $env:SMOKE_PASS = [System.Net.NetworkCredential]::new("", $sec).Password
    python build_fuelgas_reports.py
"""
import json, os, sys, urllib.request, urllib.error

BASE = os.environ.get("SMOKE_BASE", "http://127.0.0.1:8000").rstrip("/")
USER = os.environ.get("SMOKE_USER", "admin")
PASS = os.environ.get("SMOKE_PASS")
FC_A = os.environ.get("FC_A_DEVICE", "FUEL_GAS_FC001")   # FC A column
FC_B = os.environ.get("FC_B_DEVICE", "FUEL_GAS_FC002")   # FC B column

MODEL = json.loads(r"""{
 "RPage_Fuel_Gas_Current_Report.xml": {
  "subtitle": "CURRENT REPORT",
  "period": false,
  "two_col": true,
  "pages": [
   [
    {
     "type": "section",
     "name": "CURRENT DATA"
    },
    {
     "type": "data",
     "label": "STREAM STATUS",
     "unit": "",
     "reg": "STR01_STATUS_VALUE",
     "fmt": "",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "GAS VELOCITY",
     "unit": "(M/S)",
     "reg": "STR01_GAS_VELOCITY_INUSE",
     "fmt": "F3",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "LINE PRESSURE",
     "unit": "(BAR G)",
     "reg": "STR01_METER_PRESS(Pf)_INUSE",
     "fmt": "F2",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "LINE TEMPERATURE",
     "unit": "(DEG C)",
     "reg": "STR01_METER_TEMP(Tf)_INUSE",
     "fmt": "F2",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "LINE DENSITY",
     "unit": "(KG/M3)",
     "reg": "STR01_METER_DENSITY_INUSE",
     "fmt": "F3",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "STANDARD DENSITY",
     "unit": "(KG/SM3)",
     "reg": "STR01_BASE_DENS_INUSE",
     "fmt": "F4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "REAL CV",
     "unit": "(MJ/SM3)",
     "reg": "STR01_REAL_CV_INUSE",
     "fmt": "F4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "GROSS VOLUME FLOW",
     "unit": "(M3/H)",
     "reg": "STR01_UVOL_FR_INUSE",
     "fmt": "F3",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "STANDARD VOLUME FLOW",
     "unit": "(SKM3/H)",
     "reg": "STR01_CVOL_FR_INUSE",
     "fmt": "F3",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "MASS FLOW",
     "unit": "(T/H)",
     "reg": "STR01_MASS_FR_INUSE",
     "fmt": "F3",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "ENERGY FLOW",
     "unit": "(GJ/H)",
     "reg": "STR01_ENERGY_FR_INUSE",
     "fmt": "F3",
     "two": true,
     "hasB": true
    },
    {
     "type": "section",
     "name": "DAILY TOTALS"
    },
    {
     "type": "data",
     "label": "GROSS VOLUME",
     "unit": "(M3)",
     "reg": "STR01_FWD_UVOL_DAILY_TOTAL",
     "fmt": "F3",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "STANDARD VOLUME",
     "unit": "(SKM3)",
     "reg": "STR01_FWD_CVOL_DAILY_TOTAL",
     "fmt": "F3",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "MASS",
     "unit": "(T)",
     "reg": "STR01_FWD_MASS_DAILY_TOTAL",
     "fmt": "F3",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "ENERGY",
     "unit": "(GJ)",
     "reg": "STR01_FWD_ENERGY_DAILY_TOTAL",
     "fmt": "F3",
     "two": true,
     "hasB": true
    },
    {
     "type": "section",
     "name": "NON RESETTABLE TOTALS"
    },
    {
     "type": "data",
     "label": "GROSS VOLUME",
     "unit": "(M3)",
     "reg": "STR01_FWD_UVOL_TOTAL",
     "fmt": "F3",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "STANDARD VOLUME",
     "unit": "(SKM3)",
     "reg": "STR01_FWD_CVOL_TOTAL",
     "fmt": "F3",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "MASS",
     "unit": "(T)",
     "reg": "STR01_FWD_MASS_TOTAL",
     "fmt": "F3",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "ENERGY",
     "unit": "(GJ)",
     "reg": "STR01_FWD_ENERGY_TOTAL",
     "fmt": "F3",
     "two": true,
     "hasB": true
    }
   ],
   [
    {
     "type": "section",
     "name": "GAS COMPOSITION INUSE"
    },
    {
     "type": "data",
     "label": "NITROGEN",
     "unit": "(MOL %)",
     "reg": "STR01_INUSE_COMP_N2_0",
     "fmt": "F4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "CARBON DIOXIDE",
     "unit": "(MOL %)",
     "reg": "STR01_INUSE_COMP_CO2_1",
     "fmt": "F4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "METHANE",
     "unit": "(MOL %)",
     "reg": "STR01_INUSE_COMP_METHANE_5",
     "fmt": "F4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "ETHANE",
     "unit": "(MOL %)",
     "reg": "STR01_INUSE_COMP_ETHANE_6",
     "fmt": "F4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "PROPANE",
     "unit": "(MOL %)",
     "reg": "STR01_INUSE_COMP_PROPANE_7",
     "fmt": "F4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "ISO-BUTANE",
     "unit": "(MOL %)",
     "reg": "STR01_INUSE_COMP_IBUTANE_9",
     "fmt": "F4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "N-BUTANE",
     "unit": "(MOL %)",
     "reg": "STR01_INUSE_COMP_NBUTANE_8",
     "fmt": "F4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "ISO-PENTANE",
     "unit": "(MOL %)",
     "reg": "STR01_INUSE_COMP_IPENTANE_11",
     "fmt": "F4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "N-PENTANE",
     "unit": "(MOL %)",
     "reg": "STR01_INUSE_COMP_NPENTANE_10",
     "fmt": "F4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "NEO-PENTANE",
     "unit": "(MOL %)",
     "reg": "STR01_INUSE_COMP_NEOPENTANE_12",
     "fmt": "F4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "HEXANE",
     "unit": "(MOL %)",
     "reg": "STR01_INUSE_COMP_HEXANE_13",
     "fmt": "F4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "HEPTANE",
     "unit": "(MOL %)",
     "reg": "STR01_INUSE_COMP_HEPTANE_14",
     "fmt": "F4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "OCTANE",
     "unit": "(MOL %)",
     "reg": "STR01_INUSE_COMP_OCTANE_15",
     "fmt": "F4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "NONANE",
     "unit": "(MOL %)",
     "reg": "STR01_INUSE_COMP_NONANE_16",
     "fmt": "F4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "DECANE",
     "unit": "(MOL %)",
     "reg": "STR01_INUSE_COMP_DECANE_17",
     "fmt": "F4",
     "two": true,
     "hasB": true
    }
   ]
  ]
 },
 "RPage_Fuel_Gas_Daily_Report.xml": {
  "subtitle": "DAILY REPORT",
  "period": true,
  "two_col": true,
  "pages": [
   [
    {
     "type": "section",
     "name": "DAILY AVERAGE DATA"
    },
    {
     "type": "data",
     "label": "GAS VELOCITY",
     "unit": "(M/S)",
     "reg": "STR01_PD_FWA_GASVEL_4",
     "fmt": "f3",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "LINE PRESSURE",
     "unit": "(BAR G)",
     "reg": "STR01_PD_FWA_PRESSURE_4",
     "fmt": "f2",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "LINE TEMPERATURE",
     "unit": "(DEG C)",
     "reg": "STR01_PD_FWA_TEMPERATURE_4",
     "fmt": "f2",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "STREAM DENSITY",
     "unit": "(KG/M3)",
     "reg": "STR01_PD_FWA_DENSITY_4",
     "fmt": "f3",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "STANDARD DENSITY",
     "unit": "(KG/SM3)",
     "reg": "STR01_PD_FWA_STDDENS_4",
     "fmt": "f4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "REAL CV",
     "unit": "(MJ/SM3)",
     "reg": "STR01_PD_FWA_REALCV_4",
     "fmt": "f3",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "GROSS VOLUME FLOW",
     "unit": "(M3/H)",
     "reg": "STR01_PD_TWA_UVOL_FR_4",
     "fmt": "f3",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "STANDARD VOLUME FLOW",
     "unit": "(SKM3/H)",
     "reg": "STR01_PD_TWA_CVOL_FR_4",
     "fmt": "f3",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "MASS FLOW",
     "unit": "(T/H)",
     "reg": "STR01_PD_TWA_MASS_FR_4",
     "fmt": "f3",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "ENERGY FLOW",
     "unit": "(GJ/H)",
     "reg": "STR01_PD_TWA_ENERGY_FR_4",
     "fmt": "f3",
     "two": true,
     "hasB": true
    },
    {
     "type": "section",
     "name": "DAILY TOTALS"
    },
    {
     "type": "data",
     "label": "GROSS VOLUME",
     "unit": "M3",
     "reg": "STR01_FWD_UVOL_DAILY_PREVIOUS",
     "fmt": "f3",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "STANDARD VOLUME",
     "unit": "(SKM3)",
     "reg": "STR01_FWD_CVOL_DAILY_PREVIOUS",
     "fmt": "f3",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "MASS",
     "unit": "T",
     "reg": "STR01_FWD_MASS_DAILY_PREVIOUS",
     "fmt": "f3",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "ENERGY",
     "unit": "GJ",
     "reg": "STR01_FWD_ENERGY_DAILY_PREVIOUS",
     "fmt": "f3",
     "two": true,
     "hasB": true
    },
    {
     "type": "section",
     "name": "NON RESETTABLE TOTALS"
    },
    {
     "type": "data",
     "label": "GROSS VOLUME",
     "unit": "M3",
     "reg": "STR01_FWD_UVOL_DAILY_SNAPSHOT",
     "fmt": "f3",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "STANDARD VOLUME",
     "unit": "SKM3",
     "reg": "STR01_FWD_CVOL_DAILY_SNAPSHOT",
     "fmt": "f3",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "MASS",
     "unit": "T",
     "reg": "STR01_FWD_MASS_DAILY_SNAPSHOT",
     "fmt": "f3",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "ENERGY",
     "unit": "GJ",
     "reg": "STR01_FWD_ENERGY_DAILY_SNAPSHOT",
     "fmt": "f3",
     "two": true,
     "hasB": true
    }
   ],
   [
    {
     "type": "section",
     "name": "DAILY AVERAGE GAS COMPOSITION"
    },
    {
     "type": "data",
     "label": "NITROGEN",
     "unit": "",
     "reg": "STR01_PREV_D_COMP_N2_0",
     "fmt": "f4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "CARBON DIOXIDE",
     "unit": "",
     "reg": "STR01_PREV_D_COMP_CO2_1",
     "fmt": "f4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "METHANE",
     "unit": "",
     "reg": "STR01_PREV_D_COMP_METHANE_5",
     "fmt": "f4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "ETHANE",
     "unit": "",
     "reg": "STR01_PREV_D_COMP_ETHANE_6",
     "fmt": "f4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "PROPANE",
     "unit": "",
     "reg": "STR01_PREV_D_COMP_PROPANE_7",
     "fmt": "f4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "ISO-BUTANE",
     "unit": "",
     "reg": "STR01_PREV_D_COMP_IBUTANE_9",
     "fmt": "f4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "N-BUTANE",
     "unit": "",
     "reg": "STR01_PREV_D_COMP_NBUTANE_8",
     "fmt": "f4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "ISO-PENTANE",
     "unit": "",
     "reg": "STR01_PREV_D_COMP_IPENTANE_11",
     "fmt": "f4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "N-PENTANE",
     "unit": "",
     "reg": "STR01_PREV_D_COMP_NPENTANE_10",
     "fmt": "f4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "NEO-PENTANE",
     "unit": "",
     "reg": "STR01_PREV_D_COMP_NEOPENTANE_12",
     "fmt": "f4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "HEXANE",
     "unit": "",
     "reg": "STR01_PREV_D_COMP_HEXANE_13",
     "fmt": "f4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "HEPTANE",
     "unit": "",
     "reg": "STR01_PREV_D_COMP_HEPTANE_14",
     "fmt": "f4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "OCTANE",
     "unit": "",
     "reg": "STR01_PREV_D_COMP_OCTANE_15",
     "fmt": "f4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "NONANE",
     "unit": "",
     "reg": "STR01_PREV_D_COMP_NONANE_16",
     "fmt": "f4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "DECANE",
     "unit": "(MOL%)",
     "reg": "STR01_PREV_D_COMP_DECANE_17",
     "fmt": "f4",
     "two": true,
     "hasB": true
    }
   ]
  ]
 },
 "RPage_Fuel_Gas_Hourly_Report.xml": {
  "subtitle": "HOURLY REPORT",
  "period": true,
  "two_col": true,
  "pages": [
   [
    {
     "type": "section",
     "name": "HOURLY AVERAGE DATA"
    },
    {
     "type": "data",
     "label": "GAS VELOCITY",
     "unit": "(M/S)",
     "reg": "STR01_PH_FWA_GASVEL_3",
     "fmt": "f3",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "LINE PRESSURE",
     "unit": "(BAR G)",
     "reg": "STR01_PH_FWA_PRESSURE_3",
     "fmt": "f2",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "LINE TEMPERATURE",
     "unit": "(DEG C)",
     "reg": "STR01_PH_FWA_TEMPERATURE_3",
     "fmt": "f2",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "STREAM DENSITY",
     "unit": "(KG/M3)",
     "reg": "STR01_PH_FWA_DENSITY_3",
     "fmt": "f3",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "STANDARD DENSITY",
     "unit": "(KG/SM3)",
     "reg": "STR01_PH_FWA_STDDENS_3",
     "fmt": "f4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "REAL CV",
     "unit": "(MJ/SM3)",
     "reg": "STR01_PH_FWA_REALCV_3",
     "fmt": "f3",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "GROSS VOLUME FLOW",
     "unit": "(M3/H)",
     "reg": "STR01_PH_TWA_UVOL_FR_3",
     "fmt": "f3",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "STANDARD VOLUME FLOW",
     "unit": "(SKM3/H)",
     "reg": "STR01_PH_TWA_CVOL_FR_3",
     "fmt": "f3",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "MASS FLOW",
     "unit": "(T/H)",
     "reg": "STR01_PH_TWA_MASS_FR_3",
     "fmt": "f3",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "ENERGY FLOW",
     "unit": "(GJ/H)",
     "reg": "STR01_PH_TWA_ENERGY_FR_3",
     "fmt": "f3",
     "two": true,
     "hasB": true
    },
    {
     "type": "section",
     "name": "HOURLY TOTALS"
    },
    {
     "type": "data",
     "label": "GROSS VOLUME",
     "unit": "M3",
     "reg": "STR01_FWD_UVOL_HOURLY_PREVIOUS",
     "fmt": "f3",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "STANDARD VOLUME",
     "unit": "(SKM3)",
     "reg": "STR01_FWD_CVOL_HOURLY_PREVIOUS",
     "fmt": "f3",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "MASS",
     "unit": "T",
     "reg": "STR01_FWD_MASS_HOURLY_PREVIOUS",
     "fmt": "f3",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "ENERGY",
     "unit": "GJ",
     "reg": "STR01_FWD_ENERGY_HOURLY_PREVIOUS",
     "fmt": "f3",
     "two": true,
     "hasB": true
    },
    {
     "type": "section",
     "name": "NON RESETTABLE TOTALS"
    },
    {
     "type": "data",
     "label": "GROSS VOLUME",
     "unit": "M3",
     "reg": "STR01_FWD_UVOL_HOURLY_SNAPSHOT",
     "fmt": "f3",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "STANDARD VOLUME",
     "unit": "SKM3",
     "reg": "STR01_FWD_CVOL_HOURLY_SNAPSHOT",
     "fmt": "f3",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "MASS",
     "unit": "T",
     "reg": "STR01_FWD_MASS_HOURLY_SNAPSHOT",
     "fmt": "f3",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "ENERGY",
     "unit": "GJ",
     "reg": "STR01_FWD_ENERGY_DAILY_SNAPSHOT",
     "fmt": "f3",
     "two": true,
     "hasB": true
    }
   ],
   [
    {
     "type": "section",
     "name": "INTERIM AVERAGE GAS COMPOSITION"
    },
    {
     "type": "data",
     "label": "NITROGEN",
     "unit": "",
     "reg": "STR01_PREV_H_COMP_N2_0",
     "fmt": "f4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "CARBON DIOXIDE",
     "unit": "",
     "reg": "STR01_PREV_H_COMP_CO2_1",
     "fmt": "f4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "METHANE",
     "unit": "",
     "reg": "STR01_PREV_H_COMP_METHANE_5",
     "fmt": "f4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "ETHANE",
     "unit": "",
     "reg": "STR01_PREV_H_COMP_ETHANE_6",
     "fmt": "f4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "PROPANE",
     "unit": "",
     "reg": "STR01_PREV_H_COMP_PROPANE_7",
     "fmt": "f4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "ISO-BUTANE",
     "unit": "",
     "reg": "STR01_PREV_H_COMP_IBUTANE_9",
     "fmt": "f4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "N-BUTANE",
     "unit": "",
     "reg": "STR01_PREV_H_COMP_NBUTANE_8",
     "fmt": "f4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "ISO-PENTANE",
     "unit": "",
     "reg": "STR01_PREV_H_COMP_IPENTANE_11",
     "fmt": "f4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "N-PENTANE",
     "unit": "",
     "reg": "STR01_PREV_H_COMP_NPENTANE_10",
     "fmt": "f4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "NEO-PENTANE",
     "unit": "",
     "reg": "STR01_PREV_H_COMP_NEOPENTANE_12",
     "fmt": "f4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "HEXANE",
     "unit": "",
     "reg": "STR01_PREV_H_COMP_HEXANE_13",
     "fmt": "f4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "HEPTANE",
     "unit": "",
     "reg": "STR01_PREV_H_COMP_HEPTANE_14",
     "fmt": "f4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "OCTANE",
     "unit": "",
     "reg": "STR01_PREV_H_COMP_OCTANE_15",
     "fmt": "f4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "NONANE",
     "unit": "",
     "reg": "STR01_PREV_H_COMP_NONANE_16",
     "fmt": "f4",
     "two": true,
     "hasB": true
    },
    {
     "type": "data",
     "label": "DECANE",
     "unit": "(MOL%)",
     "reg": "STR01_PREV_H_COMP_DECANE_17",
     "fmt": "f4",
     "two": true,
     "hasB": true
    }
   ]
  ]
 },
 "RPage_Fuel_Gas_GC_Current_Status_Report.xml": {
  "subtitle": "CHROMATOGRAPH CURRENT STATUS REPORT",
  "period": false,
  "two_col": false,
  "pages": [
   [],
   [
    {
     "type": "data",
     "label": "NITROGEN",
     "unit": "(MOL%)",
     "reg": "STR01_INUSE_COMP_N2_0",
     "fmt": "F4",
     "two": true,
     "hasB": false
    },
    {
     "type": "data",
     "label": "CARBON DIOXIDE",
     "unit": "(MOL%)",
     "reg": "STR01_INUSE_COMP_CO2_1",
     "fmt": "F4",
     "two": true,
     "hasB": false
    },
    {
     "type": "data",
     "label": "METHANE",
     "unit": "(MOL%)",
     "reg": "STR01_INUSE_COMP_METHANE_5",
     "fmt": "F4",
     "two": true,
     "hasB": false
    },
    {
     "type": "data",
     "label": "ETHANE",
     "unit": "(MOL%)",
     "reg": "STR01_INUSE_COMP_ETHANE_6",
     "fmt": "F4",
     "two": true,
     "hasB": false
    },
    {
     "type": "data",
     "label": "PROPANE",
     "unit": "(MOL%)",
     "reg": "STR01_INUSE_COMP_PROPANE_7",
     "fmt": "F4",
     "two": true,
     "hasB": false
    },
    {
     "type": "data",
     "label": "ISO-BUTANE",
     "unit": "(MOL%)",
     "reg": "STR01_INUSE_COMP_IBUTANE_9",
     "fmt": "F4",
     "two": true,
     "hasB": false
    },
    {
     "type": "data",
     "label": "N-BUTANE",
     "unit": "(MOL%)",
     "reg": "STR01_INUSE_COMP_NBUTANE_8",
     "fmt": "F4",
     "two": true,
     "hasB": false
    },
    {
     "type": "data",
     "label": "ISO-PENTANE",
     "unit": "(MOL%)",
     "reg": "STR01_INUSE_COMP_IPENTANE_11",
     "fmt": "F4",
     "two": true,
     "hasB": false
    },
    {
     "type": "data",
     "label": "N-PENTANE",
     "unit": "(MOL%)",
     "reg": "STR01_INUSE_COMP_NPENTANE_10",
     "fmt": "F4",
     "two": true,
     "hasB": false
    },
    {
     "type": "data",
     "label": "NEO-PENTANE",
     "unit": "(MOL%)",
     "reg": "STR01_INUSE_COMP_NEOPENTANE_12",
     "fmt": "F4",
     "two": true,
     "hasB": false
    },
    {
     "type": "data",
     "label": "HEXANE",
     "unit": "(MOL%)",
     "reg": "STR01_INUSE_COMP_HEXANE_13",
     "fmt": "F4",
     "two": true,
     "hasB": false
    },
    {
     "type": "data",
     "label": "HEPTANE",
     "unit": "(MOL%)",
     "reg": "STR01_INUSE_COMP_HEPTANE_14",
     "fmt": "F4",
     "two": true,
     "hasB": false
    },
    {
     "type": "data",
     "label": "OCTANE",
     "unit": "(MOL%)",
     "reg": "STR01_INUSE_COMP_OCTANE_15",
     "fmt": "F4",
     "two": true,
     "hasB": false
    },
    {
     "type": "data",
     "label": "NONANE",
     "unit": "(MOL%)",
     "reg": "STR01_INUSE_COMP_NONANE_16",
     "fmt": "F4",
     "two": true,
     "hasB": false
    },
    {
     "type": "data",
     "label": "DECANE",
     "unit": "(MOL%)",
     "reg": "STR01_INUSE_COMP_DECANE_17",
     "fmt": "F4",
     "two": true,
     "hasB": false
    },
    {
     "type": "data",
     "label": "COMPOSITION TOTAL",
     "unit": "(MOL%)",
     "reg": "STR01_COMP_TOTAL_26",
     "fmt": "F4",
     "two": true,
     "hasB": false
    },
    {
     "type": "data",
     "label": "REAL CV",
     "unit": "(MJ/SM3)",
     "reg": "STR01_REAL_CV_INUSE",
     "fmt": "F4",
     "two": true,
     "hasB": false
    },
    {
     "type": "data",
     "label": "STANDARD DENSITY",
     "unit": "(KG/M3)",
     "reg": "STR01_BASE_DENS_INUSE",
     "fmt": "F6",
     "two": true,
     "hasB": false
    },
    {
     "type": "data",
     "label": "REAL RD",
     "unit": "",
     "reg": "STR01_REAL_RD_INUSE",
     "fmt": "F6",
     "two": true,
     "hasB": false
    },
    {
     "type": "data",
     "label": "STANDARD COMPRESS",
     "unit": "",
     "reg": "STR01_STD_COMP_INUSE",
     "fmt": "F6",
     "two": true,
     "hasB": false
    },
    {
     "type": "data",
     "label": "MOLAR MASS",
     "unit": "",
     "reg": "STR01_MOLAR_MASS_INUSE",
     "fmt": "F6",
     "two": true,
     "hasB": false
    }
   ]
  ]
 }
}""")

# ----------------------------------------------------------------- http helpers
def _req(method, path, token=None, body=None, raw=False, timeout=60):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token: req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read()
            return r.status, (payload.decode() if raw else (json.loads(payload) if payload else None))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()

def login():
    if not PASS:
        sys.exit("Set SMOKE_PASS (admin password) in the environment first.")
    st, body = _req("POST", "/api/auth/login", body={"username": USER, "password": PASS})
    if st != 200: sys.exit(f"login failed {st}: {body}")
    return body["access_token"]

def device_id(token, name):
    st, body = _req("GET", "/api/devices", token=token)
    if st != 200: sys.exit(f"GET /devices {st}: {body}")
    for d in body:
        if d.get("name") == name: return d["id"]
    sys.exit(f"device {name!r} not found — is it seeded?")

def tag_map(token, dev_id):
    """name -> id for a device (handles pagination if present)."""
    out = {}
    st, body = _req("GET", f"/api/tags?device_id={dev_id}&limit=1000", token=token)
    if st != 200: sys.exit(f"GET /tags {st}: {body}")
    rows = body if isinstance(body, list) else body.get("items", body)
    for t in rows: out[t["name"]] = t["id"]
    return out

# ------------------------------------------------------------- html generation
def fmt_places(fmt):
    fmt = (fmt or "").lower()
    return int(fmt[1:]) if (fmt.startswith("f") and fmt[1:].isdigit()) else 2

def _row(it, two_col, mapA, mapB):
    reg = it["reg"]
    dec = None if reg.endswith("STATUS_VALUE") else fmt_places(it["fmt"])
    cells = [mapA.get(reg)]
    if two_col:
        cells.append(mapB.get(reg) if it.get("hasB") else None)
    # `reg` is editor-only metadata so the Measurement dropdown shows the
    # selection; `cells` (resolved tag ids) is what the renderer uses.
    return {"label": it["label"], "unit": it["unit"], "decimals": dec, "reg": reg, "cells": cells}

def page_stream_table(items, two_col, mapA, mapB, bid, devA, devB):
    """Build one stream_table block (rows = measurements, columns = FC A / FC B)."""
    cols = [{"label": "FC A", "device_id": devA}]
    if two_col:
        cols.append({"label": "FC B", "device_id": devB})
    sections = []
    cur = None
    for it in items:
        if it["type"] == "section":
            cur = {"name": it["name"], "rows": []}
            sections.append(cur)
        elif it["type"] == "data":
            if cur is None:
                cur = {"name": "", "rows": []}
                sections.append(cur)
            cur["rows"].append(_row(it, two_col, mapA, mapB))
    return {"id": bid, "type": "stream_table", "columns": cols, "sections": sections}

def build_blocks(rep, mapA, mapB, devA, devB):
    """A no-code block list: a title (text) + a stream_table per page, page-broken."""
    pages = rep["pages"]; two = rep["two_col"]
    # Phase A: page header/footer repeat on every page. Page number goes
    # top-right ({page_of}); footer shows generation time + timezone. Because a
    # page_footer is present, the default centred counter is suppressed.
    blocks = [
        {"id": "rstyle", "type": "report_style",
         "page": {"size": "A4", "orientation": "portrait"},
         "theme": {"font_family": "Segoe UI", "font_size_pt": 10, "text_color": "#111827",
                   "heading_color": "#0B3A67", "table_header_bg": "#0B3A67",
                   "table_header_fg": "#FFFFFF", "alt_row": "#F8FAFC"},
         "time": {"basis": "system", "format": "%d-%b-%Y %H:%M", "show_suffix": True}},
        {"id": "pghdr", "type": "page_header",
         "left": "", "center": "", "right": "{page_of}"},
        {"id": "pgftr", "type": "page_footer",
         "left": "Generated {generated_at}", "center": "", "right": "{timezone}"},
    ]
    for i, items in enumerate(pages):
        # No hardcoded "PAGE x OF y" — compile_blocks injects an @page footer
        # with counter(page)/counter(pages), so numbering is automatic and
        # stays correct no matter how the content paginates.
        title = "FUEL GAS \u00b7 MARINER PULQ METERING SYSTEM \u00b7 %s" % rep["subtitle"]
        blocks.append({"id": "ttl%d" % (i + 1), "type": "text", "content": title})
        blocks.append(page_stream_table(items, two, mapA, mapB, "st%d" % (i + 1), devA, devB))
        if i < len(pages) - 1:
            blocks.append({"id": "pb%d" % (i + 1), "type": "page_break"})
    return blocks

def referenced_ids(rep, mapA, mapB):
    ids = set()
    for items in rep["pages"]:
        for it in items:
            if it["type"] != "data": continue
            if it["reg"] in mapA: ids.add(mapA[it["reg"]])
            if rep["two_col"] and it.get("hasB") and it["reg"] in mapB: ids.add(mapB[it["reg"]])
    return sorted(ids)

# ----------------------------------------------------------------- definitions
def find_def(token, name):
    st, body = _req("GET", "/api/report-config/definitions", token=token)
    if st != 200: sys.exit(f"GET /definitions {st}: {body}")
    rows = body if isinstance(body, list) else body.get("items", body)
    for d in rows:
        if d.get("name") == name: return d["id"]
    return None

def upsert_def(token, name, blocks, category):
    payload = {"name": name, "category": category,
               "report_type": "fuel_gas",
               "template_mode": "blocks", "template_blocks": blocks}
    did = find_def(token, name)
    if did:
        st, body = _req("PATCH", f"/api/report-config/definitions/{did}", token=token, body=payload)
        if st not in (200, 204): sys.exit(f"PATCH def {did} {st}: {body}")
        return did
    st, body = _req("POST", "/api/report-config/definitions", token=token, body=payload)
    if st not in (200, 201): sys.exit(f"POST def {st}: {body}")
    return body["id"]

def bind_tags(token, did, ids):
    st, body = _req("PUT", f"/api/report-config/definitions/{did}/tags", token=token,
                    body={"tag_ids": ids})
    if st not in (200, 204): sys.exit(f"bind tags {st}: {body}")

def preview(token, did, blocks):
    st, html = _req("POST", f"/api/report-config/definitions/{did}/preview",
                    token=token, raw=True, timeout=25,
                    body={"template_mode": "blocks", "template_blocks": blocks, "force_live": True})
    return st, html

# ------------------------------------------------------------------------ main
def main():
    token = login()
    devA, devB = device_id(token, FC_A), device_id(token, FC_B)
    mapA, mapB = tag_map(token, devA), tag_map(token, devB)
    print(f"FC A {FC_A} id={devA} ({len(mapA)} tags) | FC B {FC_B} id={devB} ({len(mapB)} tags)")

    here = os.path.dirname(os.path.abspath(__file__))
    NAME = {"RPage_Fuel_Gas_Current_Report.xml": "Fuel Gas - Current Report",
            "RPage_Fuel_Gas_Daily_Report.xml": "Fuel Gas - Daily Report",
            "RPage_Fuel_Gas_Hourly_Report.xml": "Fuel Gas - Hourly Report",
            "RPage_Fuel_Gas_GC_Current_Status_Report.xml": "Fuel Gas - Chromatograph Current Status"}

    for fn, rep in MODEL.items():
        name = NAME[fn]
        blocks = build_blocks(rep, mapA, mapB, devA, devB)
        ids = referenced_ids(rep, mapA, mapB)
        category = "periodic" if rep.get("period") else "on_demand"
        did = upsert_def(token, name, blocks, category)
        bind_tags(token, did, ids)
        missing = sum(1 for items in rep["pages"] for it in items
                      if it["type"] == "data" and it["reg"] not in mapA)
        note = f" (+{missing} cells need template tags)" if missing else ""
        print(f"  [OK] {name!r}  def#{did}  {len(ids)} tags bound{note}")
        try:
            st, html = preview(token, did, blocks)
        except Exception as e:
            print(f"        preview skipped ({type(e).__name__}); report is still created — preview it in the UI")
            continue
        if st == 200:
            outp = os.path.join(here, name.replace(" ", "_").replace("-", "") + ".preview.html")
            open(outp, "w", encoding="utf-8").write(html)
            print(f"        preview -> {outp}")
        else:
            print(f"        preview HTTP {st}: {str(html)[:200]} (report still created)")

    print("\nDone. Reports are created/updated as stream_table blocks. Preview them in the UI "
          "under Reports \u2192 report_type 'fuel_gas'.")

if __name__ == "__main__":
    main()
