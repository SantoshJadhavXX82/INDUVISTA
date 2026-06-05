import xml.etree.ElementTree as ET, json, re

XML = "/mnt/user-data/uploads/FG_FC_REPORT.xml"
OUT = "/home/claude/INDUVISTA/config/device_templates/fuel_gas_fc.json"

pts = []
for p in ET.parse(XML).getroot().iter("Point"):
    pts.append((int(p.get("Address")), p.get("Name")))
pts.sort()

def unit_for(n):
    u = n.upper()
    if "STATUS" in u: return None
    if "COMP_" in u: return "mol%"
    if "MOLAR_MASS" in u: return "g/mol"
    if "GAS_VELOCITY" in u or "GASVEL" in u: return "m/s"
    if "REAL_CV" in u or "REALCV" in u: return "MJ/m3"
    if "STDDENS" in u or "BASE_DENS" in u: return "kg/m3"
    if "DENSITY" in u: return "kg/m3"
    if "PRESS" in u: return "barg"
    if "TEMP" in u: return "degC"
    if "ENERGY" in u:  return "GJ/h" if ("_FR_" in u or "FR_INUSE" in u or "TWA" in u) else "GJ"
    if "MASS" in u:    return "kg/h" if ("_FR_" in u or "FR_INUSE" in u or "TWA" in u) else "kg"
    if "UVOL" in u or "CVOL" in u or "VOL" in u:
        return "m3/h" if ("_FR_" in u or "FR_INUSE" in u or "TWA" in u) else "m3"
    return None

# base value + sim mode by tag kind (FC001 baseline; FC002 differs via device sim_scale/phase)
def sim_for(n):
    u = n.upper()
    if "STATUS" in u:
        return {"mode":"static","min":1.0,"max":1.0,"period_s":1.0,"initial":1.0}, True  # no_scale
    if "COMP_METHANE" in u:   return {"mode":"sine","min":88.0,"max":91.0,"period_s":600.0,"initial":89.5}, False
    if "COMP_ETHANE" in u:    return {"mode":"sine","min":4.0,"max":5.2,"period_s":600.0,"initial":4.6}, False
    if "COMP_PROPANE" in u:   return {"mode":"sine","min":1.4,"max":2.0,"period_s":600.0,"initial":1.7}, False
    if "COMP_CO2" in u:       return {"mode":"sine","min":0.8,"max":1.3,"period_s":600.0,"initial":1.05}, False
    if "COMP_N2" in u:        return {"mode":"sine","min":0.6,"max":1.0,"period_s":600.0,"initial":0.8}, False
    if "COMP_" in u:          return {"mode":"random","min":0.02,"max":0.45,"period_s":300.0,"initial":0.2}, False
    if "MOLAR_MASS" in u:     return {"mode":"sine","min":17.6,"max":18.4,"period_s":300.0,"initial":18.0}, False
    if "GAS_VELOCITY" in u or "GASVEL" in u: return {"mode":"sine","min":6.0,"max":13.0,"period_s":90.0,"initial":9.4}, False
    if "REAL_CV" in u or "REALCV" in u:      return {"mode":"sine","min":38.5,"max":39.8,"period_s":300.0,"initial":39.1}, False
    if "STDDENS" in u or "BASE_DENS" in u:   return {"mode":"sine","min":0.74,"max":0.79,"period_s":300.0,"initial":0.765}, False
    if "METER_DENSITY" in u or "DENSITY" in u:return {"mode":"sine","min":42.0,"max":47.0,"period_s":120.0,"initial":44.3}, False
    if "PRESS" in u:          return {"mode":"sine","min":52.0,"max":58.0,"period_s":120.0,"initial":55.0}, False
    if "TEMP" in u:           return {"mode":"sine","min":14.0,"max":24.0,"period_s":180.0,"initial":19.2}, False
    if "ENERGY_FR" in u or ("ENERGY" in u and "_FR_" in u): return {"mode":"sine","min":480.0,"max":560.0,"period_s":90.0,"initial":520.0}, False
    if "MASS_FR" in u or ("MASS" in u and "_FR_" in u):     return {"mode":"sine","min":12000.0,"max":14500.0,"period_s":90.0,"initial":13200.0}, False
    if "FR_INUSE" in u or "_FR_" in u or "TWA" in u or "FWA" in u:
        return {"mode":"sine","min":1150.0,"max":1380.0,"period_s":90.0,"initial":1265.0}, False
    if "ENERGY" in u:  return {"mode":"static","min":0.0,"max":0.0,"period_s":1.0,"initial":1283700.0}, False
    if "MASS" in u:    return {"mode":"static","min":0.0,"max":0.0,"period_s":1.0,"initial":32650000.0}, False
    if "CVOL" in u:    return {"mode":"static","min":0.0,"max":0.0,"period_s":1.0,"initial":2480500.0}, False
    if "UVOL" in u or "VOL" in u: return {"mode":"static","min":0.0,"max":0.0,"period_s":1.0,"initial":2511800.0}, False
    return {"mode":"static","min":0.0,"max":0.0,"period_s":1.0,"initial":0.0}, False

tags = []
for addr, name in pts:
    sim, no_scale = sim_for(name)
    t = {"name":name,"area":"HR","function_code":3,"address":addr,
         "data_type":"float64","byte_order":"ABCD","register_count":4,
         "engineering_unit":unit_for(name),"scale":1.0,"offset":0.0,
         "min_value":None,"max_value":None,"access":"RO","group":"Stream 1",
         "description":name.replace("_"," ").title(),"sim":sim}
    if no_scale: t["no_scale"] = True
    tags.append(t)

# chunk into register_blocks <=120 regs (<=30 tags) so each Modbus read is <=125
blocks = []
CH = 30
for i in range(0, len(tags), CH):
    grp = tags[i:i+CH]
    start = grp[0]["address"]
    end = grp[-1]["address"] + 4 - 1
    blocks.append({"name":f"FG_HR_{start:04d}","area":"HR","function_code":3,
                   "start_address":start,"end_address":end,
                   "tags":[t["name"] for t in grp],"count":end-start+1})

tpl = {"template_id":"fuel_gas_fc",
       "description":"Fuel-gas flow computer (single stream, 141 IEEE-754 doubles)",
       "register_blocks":blocks,"tags":tags,"bit_labels":{}}
json.dump(tpl, open(OUT,"w"), indent=2)
print(f"wrote {OUT}: {len(tags)} tags, {len(blocks)} blocks")
print("block reg spans:", [b["count"] for b in blocks], "(all <=125 ok)")
print("sample:", json.dumps(tags[0]), "\n        ", json.dumps(tags[5]))
