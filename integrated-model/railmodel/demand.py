"""Optional bottom-up modules: no Sabah rates or calibrated parameters invented."""
import math
from .maths import mode_choice


def generation(records):
    """Quantum x rate x non-overlap weight; exact matching quantum/rate denominator."""
    outputs=[]
    for r in records:
        required=['id','zone','quantum','quantum_unit','rate','rate_denominator_unit','overlap_weight','source','status']
        if any(k not in r for k in required):raise ValueError('Trip generation requires complete provenance and units')
        if r['quantum_unit']!=r['rate_denominator_unit']:raise ValueError('Rate/quantum unit mismatch')
        if not 0<=r['overlap_weight']<=1 or r['quantum']<0 or r['rate']<0:raise ValueError('Invalid quantum, rate or catchment overlap weight')
        outputs.append({**r,'trips':r['quantum']*r['rate']*r['overlap_weight']})
    return outputs


def capture(total_person_trips, alternatives, beta_time, beta_cost):
    """MNL using user-supplied coefficients; conditional use, never a calibrated claim."""
    if total_person_trips<0 or beta_time>0 or beta_cost>0:raise ValueError('Invalid demand or disutility coefficients')
    if any(not all(k in r for k in ['mode','minutes','cost','asc','source','status']) for r in alternatives):raise ValueError('Missing utility metadata')
    utilities={r['mode']:r['asc']+beta_time*r['minutes']+beta_cost*r['cost'] for r in alternatives}
    if len(utilities)!=len(alternatives):raise ValueError('Duplicate modes')
    shares=mode_choice(utilities)
    return [{'mode':k,'share':v,'person_trips':v*total_person_trips,'status':'Parameterised; calibration required'} for k,v in shares.items()]


def freight_operations(markets, days, effective_teu_per_train=None, net_tonnes_per_train=None):
    """Daily integer round trips by service: empty backhaul uses a path too."""
    if days<=0:raise ValueError('Positive operating days required')
    services={}
    for r in markets:
        service=r.get('service')
        if not service:continue
        if r['origin']==r['destination']:continue
        q=services.setdefault(service,{'A':0.0,'B':0.0,'unit':'TEU' if r['teu_year'] else 't'})
        direction=r.get('direction','A')
        q[direction]+=r['teu_year'] if r['teu_year'] else r['tonnes_year']
    result=[]
    for k,v in services.items():
        capacity=effective_teu_per_train if v['unit']=='TEU' else net_tonnes_per_train
        if capacity is not None and capacity<=0:raise ValueError('Positive train capacity required')
        trips=math.ceil(max(v['A'],v['B'])/days/capacity) if capacity else None
        result.append({'service':k,'annual_A':v['A'],'annual_B':v['B'],'unit':v['unit'],
            'effective_capacity':capacity,'round_trips_day':trips,'paths_day':2*trips if trips is not None else None,
            'status':'Volume sizing only; timetable, trailing-load, axle-load and length validation pending'})
    return result
