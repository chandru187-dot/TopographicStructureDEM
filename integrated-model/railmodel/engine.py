"""Modular, evidence-controlled scenario engine. Missing values remain None."""
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import uuid
from . import VERSION
from .maths import interpolate, rail_loading
from .demand import freight_operations


@dataclass
class Scenario:
    year: int = 2033
    case: str = "Base"
    passenger_variant: str = "summary_draft"
    freight_variant: str = "v47_draft"
    freight_package: str = "S2"
    option: int = 3
    opening_year: int = 2033
    overrides: dict = field(default_factory=dict)
    station_od: dict | None = None


class Engine:
    def __init__(self, data_path=None):
        version=os.getenv('MODEL_DATABASE_DATA_VERSION')
        if version and data_path is None:
            import psycopg
            with psycopg.connect(os.environ['DATABASE_URL']) as c:
                row=c.execute('SELECT payload FROM dataset_version WHERE data_version=%s',(version,)).fetchone()
            if row is None:raise ValueError('Pinned database evidence version missing')
            self.data=row[0];self.data_version=version;self.path=None
            return
        self.path = Path(data_path or os.getenv("MODEL_DATA_PATH", "data/project.json"))
        self.data = json.loads(self.path.read_text())
        self.data_version = sha256(self.path.read_bytes()).hexdigest()

    def run(self, scenario):
        s = scenario if isinstance(scenario,Scenario) else Scenario(**scenario)
        if s.case not in ("Low","Base","High") or s.option not in range(1,8):
            raise ValueError("Invalid case or option")
        if s.freight_package not in ("S1","S2","S3"):
            raise ValueError("Invalid freight package")
        if s.freight_variant not in ("v47_draft","tn3_headline") or s.passenger_variant not in ("summary_draft","tn3_high_only"):
            raise ValueError("Unknown evidence variant")
        if not 2024 <= s.year <= 2063 or not 2024 <= s.opening_year <= 2063:
            raise ValueError("Year outside evidence envelope")
        permitted = {
            "passenger_days":(1,366),"freight_days":(1,366),"private_diversion_share":(0,1),
            "occupancy":(1,100),"passenger_distance_km":(0,1000),"truck_payload_t":(0.1,100),
            "freight_distance_km":(0,3000),"freight_road_diversion_share":(0,1),
            "freight_feeder_vkt":(0,1e12),"passenger_access_vkt":(0,1e12),
            "headway_min":(1,180),"reference_headway_min":(1,180),"headway_elasticity":(-2,0),
            "train_capacity":(1,10000),"service_hours":(1,24),"peak_share":(0,1),
            "freight_train_payload_t":(1,5000),"rail_invehicle_min":(0,1000),"access_min":(0,1000),
            "egress_min":(0,1000),"transfer_min":(0,1000),"road_door_min":(0,1000),
            "value_time_rm_hour":(0,10000),"car_voc_rm_km":(0,100),"truck_voc_rm_km":(0,100),
            "net_freight_saving_rm_tkm":(0,100),"crash_rate_per_million_vkt":(0,1000),
            "baseline_annual_vkt":(1,1e14),"future_annual_vkt":(1,1e14),
            "crash_cost_rm":(0,1e9),"fare_rm_trip":(0,1000),"freight_tariff_rm_t":(0,10000),
            "annual_rail_operating_cost_rm":(0,1e12),"freight_capture_multiplier":(0,10),
            "effective_teu_per_train":(0.1,1000),"net_tonnes_per_freight_train":(0.1,5000)
        }
        for key, value in s.overrides.items():
            if key not in permitted or not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value) or not permitted[key][0] <= value <= permitted[key][1]:
                raise ValueError(f"Invalid override: {key}")
        # Core operating conventions retain their documentary draft status.
        a = {"passenger_days":365,"freight_days":300,"headway_min":20,
             "reference_headway_min":20,"service_hours":16,"opening_year":s.opening_year}
        a.update(s.overrides)
        warnings = ["DRAFT evidence run: no approved passenger/freight project baseline established",
                    "Passenger source counts catchment IN + OUT; unique journey interpretation unresolved",
                    "Passenger peak forecast and daily forecast use different growth calculations in source workbook",
                    "Survey sample rows differ from TN3 reporting sample; no population expansion weights available"]
        trace=[]
        def record(key, value, unit, equation, sources, status="Draft", deps=None):
            trace.append({"output":key,"value":value,"unit":unit,"equation":equation,
                          "sources":sources,"status":status,"dependencies":deps or []})
            return value
        idx = ("Low","Base","High").index(s.case)
        forecasts = self.data['passenger_forecasts'][s.passenger_variant]
        if s.passenger_variant == "tn3_high_only" and (s.case != "High" or s.option != 3):
            raise ValueError("TN3 table supports High / Option 3 only")
        points = forecasts[s.case][str(s.option)]
        p = interpolate(points,s.year)
        record("passenger_source_daily",p,"source trips/day","linear interpolation of selected source schedule",["P_SUMMARY" if s.passenger_variant=="summary_draft" else "TN3"])
        if str(s.year) not in points:
            warnings.append("Passenger forecast uses explicit linear interpolation; not an observed forecast-year value")
        factor = 1.0
        if a['headway_min'] != a['reference_headway_min']:
            if 'headway_elasticity' in a:
                factor = (a['headway_min']/a['reference_headway_min'])**a['headway_elasticity']
                warnings.append("Headway demand response is an uncalibrated user sensitivity")
            else:
                warnings.append("Headway changes capacity/waiting only: ridership elasticity not calibrated")
        active = s.year >= s.opening_year
        p = p*factor if active else 0.0
        annual = record("passenger_annual",p*a['passenger_days'],"source trips/year","daily x passenger operating days",["P_SUMMARY","USER_OVERRIDES"],deps=["passenger_source_daily"])
        peak = interpolate(self.data['passenger_peaks'][s.case][str(s.option)],s.year)*factor if active else 0.0
        capacity_hr = 60/a['headway_min']*a['train_capacity'] if 'train_capacity' in a else None
        utilisation = peak/capacity_hr if capacity_hr else None
        if utilisation and utilisation > 1:
            warnings.append("Source PPHPD exceeds supplied hourly train capacity")
        operations = {"headway_min":a['headway_min'],"trains_per_hour_direction":60/a['headway_min'],
                      "service_hours":a['service_hours'],"departures_day_both_directions":2*a['service_hours']*60/a['headway_min'],
                      "source_pphpd":peak,"capacity_pphpd":capacity_hr,"source_peak_utilisation":utilisation,
                      "status":"Source PPHPD is not a station-pair assigned line load"}
        freight = self.freight(s,a,idx,trace,warnings) if active else {"tonnes_year":0.0,"teu_year":0.0,"markets":[],"od":[],"status":"Pre-opening"}
        f = freight['tonnes_year']
        fd = f/a['freight_days']
        operations['freight_services']=freight_operations(freight['markets'],a['freight_days'],a.get('effective_teu_per_train'),a.get('net_tonnes_per_freight_train'))
        record("freight_tonnes_year",f,"tonnes/year","sum captured market cargo; no recapture of already captured headlines",["F47" if s.freight_variant=="v47_draft" else "TN3"])
        diverted_f = sum(r['tonnes_year'] for r in freight['markets'] if not r.get('existing_rail',False)) if s.freight_variant=='v47_draft' else f
        pv = p*a['private_diversion_share']/a['occupancy'] if all(k in a for k in ['private_diversion_share','occupancy']) else None
        tv = diverted_f*a['freight_road_diversion_share']/a['freight_days']/a['truck_payload_t'] if all(k in a for k in ['freight_road_diversion_share','truck_payload_t']) else None
        if pv is None:
            warnings.append("Private-road diversion/occupancy unresolved: vehicle reduction withheld")
        gross_pvkt = pv*a['passenger_days']*a['passenger_distance_km'] if pv is not None and 'passenger_distance_km' in a else None
        gross_tvkt = tv*a['freight_days']*a['freight_distance_km'] if tv is not None and 'freight_distance_km' in a else None
        net_pvkt = gross_pvkt-a['passenger_access_vkt'] if gross_pvkt is not None and 'passenger_access_vkt' in a else None
        net_tvkt = gross_tvkt-a['freight_feeder_vkt'] if gross_tvkt is not None and 'freight_feeder_vkt' in a else None
        total_vkt = net_pvkt+net_tvkt if net_pvkt is not None and net_tvkt is not None else None
        record("net_vkt_avoided",total_vkt,"vehicle-km/year","gross diverted VKT - rail access/egress and freight feeder VKT",["USER_OVERRIDES"],"Unresolved" if total_vkt is None else "Draft")
        traffic={"private_vehicle_trips_removed_day":pv,"truck_trips_removed_day":tv,
                 "existing_rail_freight_excluded_tonnes":f-diverted_f,
                 "gross_passenger_vkt_avoided":gross_pvkt,"gross_truck_vkt_avoided":gross_tvkt,
                 "net_passenger_vkt_avoided":net_pvkt,"net_truck_vkt_avoided":net_tvkt,
                 "net_vkt_avoided":total_vkt,"network_assignment_status":"Not calibrated; no assigned road flows"}
        safety={"observed_accidents":self.data['accidents']['total'],"observed_years":7,
                "historical_annual_accidents":self.data['accidents']['total']/7,
                "accidents_avoided_year":None,"status":"Unresolved exposure denominator"}
        if total_vkt is not None and 'baseline_annual_vkt' in a and 'future_annual_vkt' in a:
            baseline = safety['historical_annual_accidents']
            rate = baseline/a['baseline_annual_vkt']
            future_crashes = rate*a['future_annual_vkt']
            if total_vkt > a['future_annual_vkt']:
                raise ValueError("Avoided VKT exceeds comparable future road exposure")
            safety.update(accidents_avoided_year=total_vkt*rate,
                          future_without_rail_accidents=future_crashes,
                          status="Exposure-rate screening, constant risk assumption",rate_per_vkt=rate)
        elif total_vkt is not None and 'crash_rate_per_million_vkt' in a:
            safety.update(accidents_avoided_year=total_vkt*a['crash_rate_per_million_vkt']/1e6,
                          status="Unvalidated rate sensitivity; excluded from economic total")
            warnings.append("Unvalidated crash-rate sensitivity excluded from appraisal")
        record("accidents_avoided_year",safety['accidents_avoided_year'],"accidents/year","net avoided VKT x comparable historical accidents/VKT",["TN2","USER_OVERRIDES"],"Unresolved" if safety['accidents_avoided_year'] is None else "Draft")
        rail_door = a['rail_invehicle_min']+a['access_min']+a['egress_min']+a['transfer_min']+a['headway_min']/2 if all(k in a for k in ['rail_invehicle_min','access_min','egress_min','transfer_min']) else None
        saving = a['road_door_min']-rail_door if rail_door is not None and 'road_door_min' in a else None
        components = {
            "passenger_time": annual*saving/60*a['value_time_rm_hour'] if saving is not None and 'value_time_rm_hour' in a else None,
            "car_voc":net_pvkt*a['car_voc_rm_km'] if net_pvkt is not None and 'car_voc_rm_km' in a else None,
            "truck_voc":net_tvkt*a['truck_voc_rm_km'] if net_tvkt is not None and 'truck_voc_rm_km' in a else None,
            "net_freight_transport":diverted_f*a['freight_distance_km']*a['net_freight_saving_rm_tkm'] if all(k in a for k in ['freight_distance_km','net_freight_saving_rm_tkm']) else None,
            "safety":safety['accidents_avoided_year']*a['crash_cost_rm'] if safety['status'].startswith("Exposure-rate") and 'crash_cost_rm' in a else None
        }
        economic = {"rail_door_min":rail_door,"time_saving_min":saving,"annual_components_rm":components,
                    "quantified_subtotal_rm":sum(v for v in components.values() if v is not None),
                    "complete_annual_benefit_rm":sum(components.values()) if all(v is not None for v in components.values()) else None,
                    "appraisal_status":"No project NPV/BCR without cost stream, counterfactual and approved unit values",
                    "method_status":"Existing-user time savings screening; generated-trip rule-of-half pending transfer split"}
        revenue = {"passenger_revenue_rm":annual*a['fare_rm_trip'] if 'fare_rm_trip' in a else None,
                   "freight_revenue_rm":f*a['freight_tariff_rm_t'] if 'freight_tariff_rm_t' in a else None,
                   "operating_surplus_rm":None}
        if all(revenue[k] is not None for k in ['passenger_revenue_rm','freight_revenue_rm']) and 'annual_rail_operating_cost_rm' in a:
            revenue['operating_surplus_rm']=revenue['passenger_revenue_rm']+revenue['freight_revenue_rm']-a['annual_rail_operating_cost_rm']
        loading=None
        if s.station_od is not None:
            if 'status' not in s.station_od or 'source' not in s.station_od:
                raise ValueError("Station OD requires explicit source/status")
            loading=rail_loading(s.station_od['matrix'],s.station_od['station_ids'])
            if not math.isclose(loading['daily_trips'],p,rel_tol=1e-6,abs_tol=1e-6):
                raise ValueError("Station OD does not reconcile with selected passenger daily total")
            loading['status']=s.station_od['status'];loading['source']=s.station_od['source']
        else:
            warnings.append("Station OD not established; no synthetic boardings/line loads reported as facts")
        checks=[{"check":"freight market total","status":"PASS" if math.isclose(sum(r['tonnes_year'] for r in freight['markets']),f,rel_tol=1e-9,abs_tol=1e-6) else "FAIL"},
                {"check":"station balance","status":"UNRESOLVED" if loading is None else "PASS"},
                {"check":"passenger annualisation","status":"PASS" if math.isclose(annual,p*a['passenger_days'],rel_tol=1e-12) else "FAIL"},
                {"check":"capacity","status":"UNRESOLVED" if utilisation is None else "FAIL" if utilisation>1 else "PASS"},
                {"check":"safety exposure","status":"PASS" if safety['status'].startswith('Exposure-rate') else "UNRESOLVED"},
                {"check":"baseline approval","status":"UNRESOLVED"}]
        record("passenger_revenue_rm",revenue['passenger_revenue_rm'],"RM/year","annual passenger trips x fare",["USER_OVERRIDES"],deps=["passenger_annual"])
        return {"run_id":str(uuid.uuid4()),"timestamp":datetime.now(timezone.utc).isoformat(),
                "model_version":VERSION,"git_version":os.getenv('MODEL_GIT_SHA','uncommitted-build'),
                "data_version":self.data_version,"scenario":asdict(s),"effective_assumptions":a,
                "passenger":{"daily_source_trips":p,"annual_source_trips":annual,"headway_factor":factor,"status":"Draft source reproduction"},
                "freight":freight,"traffic":traffic,"operations":operations,"station_loading":loading,
                "safety":safety,"economic":economic,"financial":revenue,"trace":trace,
                "checks":checks,"warnings":warnings,
                "validation_status":"FAILED" if any(c['status']=='FAIL' for c in checks) else "DRAFT_PARTIAL",
                "input_snapshot":self.data}

    def freight(self,s,a,idx,trace,warnings):
        if s.freight_variant=='tn3_headline':
            value=interpolate(self.data['freight_headline'][s.case],s.year)
            warnings.append("TN3 freight headline alternative: no market OD or package disaggregation attached")
            row={"id":"TN3_HEADLINE","origin":"Unresolved","destination":"Unresolved","tonnes_year":value,"teu_year":None,"existing_rail":False}
            return {"tonnes_year":value,"teu_year":None,"markets":[row],"od":[],"status":"Draft headline alternative"}
        controls=self.data['freight_controls']
        throughput = min(controls['port_actual']*(1+controls['growth'][idx])**(s.year-controls['port_year']),controls['capacity'])
        land = throughput*controls['land_share']
        ramp = interpolate({s.opening_year:0.4,s.opening_year+5:0.8,s.opening_year+10:1},min(s.year,s.opening_year+10))
        rows=[]
        allowed={'S1'} if s.freight_package=='S1' else {'S1','S2'} if s.freight_package=='S2' else {'S1','S2','S3'}
        for r in self.data['freight_markets']:
            if r['package'] not in allowed or not r['included']:
                continue
            if r['market_volume'] is None:
                warnings.append(f"{r['id']} market unquantified; excluded, never interpreted as confirmed zero")
                continue
            market = land*r['port_allocation'] if r['port_allocation'] is not None else r['market_volume']*(1+r['growth'][idx])**(s.year-r['base_year'])
            factors = [r[k][idx] for k in ['development','rail_suitability','commodity_suitability','terminal','capture']]
            if any(x is None for x in factors):
                warnings.append(f"{r['id']} missing capture factors");continue
            capture=min(1,factors[-1]*a.get('freight_capture_multiplier',1))
            volume=market*math.prod(factors[:-1])*capture*(ramp if r['ramp'] else 1)
            teu=volume if r['container'] else 0
            cargo=volume*r['laden_share']*r['cargo_per_laden_teu'] if r['container'] else volume
            row={"id":r['id'],"origin":r['origin'],"destination":r['destination'],"service":r['service'],"direction":r['direction'],
                 "market":r['commodity'],"market_volume":market,"market_unit":r['unit'],"factors":factors[:-1]+[capture],
                 "ramp":ramp if r['ramp'] else 1,"teu_year":teu,"tonnes_year":cargo,
                 "existing_rail":r['service']=='SV5',"source_cell":r['source_cell'],"status":"Draft market allocation"}
            rows.append(row)
            trace.append({"output":r['id'],"value":cargo,"unit":"tonnes/year","equation":"market x development x rail suitability x commodity suitability x terminal x capture x ramp x laden share x t/laden TEU","sources":["F47"],"source_cell":r['source_cell'],"status":"Draft"})
        od={}
        for r in rows:
            k=(r['origin'],r['destination'])
            q=od.setdefault(k,{"origin":k[0],"destination":k[1],"tonnes_year":0,"teu_year":0})
            q['tonnes_year']+=r['tonnes_year'];q['teu_year']+=r['teu_year']
        return {"tonnes_year":sum(r['tonnes_year'] for r in rows),"teu_year":sum(r['teu_year'] for r in rows),
                "markets":rows,"od":list(od.values()),"port_throughput_teu":throughput,
                "status":"Python reproduction of v4.7 selected port-allocation draft; package distinct from Low/Base/High"}
