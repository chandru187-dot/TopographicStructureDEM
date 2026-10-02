"""Transport primitives, with explicit units and no project defaults."""
import math


def interpolate(points, year):
    """Linear interpolation only inside a supplied evidence envelope."""
    p = sorted((int(k), float(v)) for k, v in points.items())
    if not p or year < p[0][0] or year > p[-1][0]:
        raise ValueError("Forecast year outside source envelope; extrapolation disabled")
    for y, v in p:
        if year == y:
            return v
    for (a, x), (b, z) in zip(p, p[1:]):
        if a < year < b:
            return x + (z-x)*(year-a)/(b-a)
    raise ValueError("Empty forecast envelope")


def ipf(seed, productions, attractions, tolerance=1e-8, max_iterations=10000):
    """Furness balancing: do not invent attraction totals or structural-zero flows."""
    n, m = len(productions), len(attractions)
    if len(seed) != n or any(len(r) != m for r in seed):
        raise ValueError("Matrix dimensions disagree")
    vals = list(productions)+list(attractions)+[v for r in seed for v in r]
    if any(not math.isfinite(v) or v < 0 for v in vals):
        raise ValueError("Demand must be finite and non-negative")
    total = sum(productions)
    if not math.isclose(total, sum(attractions), rel_tol=tolerance, abs_tol=tolerance):
        raise ValueError("Productions and attractions disagree; explicit balancing required")
    x = [list(map(float, r)) for r in seed]
    if total == 0:
        return [[0.0]*m for _ in range(n)], 0
    for iteration in range(max_iterations):
        for i in range(n):
            s = sum(x[i])
            if s == 0 and productions[i] > 0:
                raise ValueError("Infeasible production structural zeros")
            f = productions[i]/s if s else 0
            x[i] = [v*f for v in x[i]]
        for j in range(m):
            s = sum(x[i][j] for i in range(n))
            if s == 0 and attractions[j] > 0:
                raise ValueError("Infeasible attraction structural zeros")
            f = attractions[j]/s if s else 0
            for i in range(n):
                x[i][j] *= f
        error = max([abs(sum(x[i])-productions[i]) for i in range(n)] +
                    [abs(sum(x[i][j] for i in range(n))-attractions[j]) for j in range(m)])
        if error <= tolerance*max(1, total):
            return x, iteration+1
    raise ValueError("OD balancing did not converge")


def gravity(productions, attractions, travel_minutes, beta_per_minute):
    if beta_per_minute <= 0 or any(v < 0 or not math.isfinite(v) for r in travel_minutes for v in r):
        raise ValueError("Invalid friction or skim")
    seed = [[math.exp(-beta_per_minute*t) if i != j else 0.0
             for j, t in enumerate(row)] for i, row in enumerate(travel_minutes)]
    return ipf(seed, productions, attractions)


def mode_choice(utilities):
    """Stable multinomial logit. Parameters must be independently calibrated."""
    if not utilities or any(not math.isfinite(v) for v in utilities.values()):
        raise ValueError("Finite utilities required")
    top = max(utilities.values())
    weights = {k: math.exp(v-top) for k, v in utilities.items()}
    return {k: v/sum(weights.values()) for k, v in weights.items()}


def rail_loading(matrix, station_ids):
    """Directed daily line loads for one ordered line; passengers counted once."""
    n = len(station_ids)
    if len(matrix) != n or any(len(r) != n for r in matrix):
        raise ValueError("Station matrix dimensions disagree")
    if any(v < 0 or not math.isfinite(v) for r in matrix for v in r):
        raise ValueError("Invalid station demand")
    if any(matrix[i][i] != 0 for i in range(n)):
        raise ValueError("Intrastation trips cannot ride a line")
    boards = [sum(r) for r in matrix]
    alights = [sum(matrix[i][j] for i in range(n)) for j in range(n)]
    links = []
    for k in range(n-1):
        forward = sum(matrix[i][j] for i in range(k+1) for j in range(k+1,n))
        reverse = sum(matrix[i][j] for i in range(k+1,n) for j in range(k+1))
        links.append({"from":station_ids[k],"to":station_ids[k+1],
                      "forward_passengers":forward,"reverse_passengers":reverse})
    return {"stations":[{"id":s,"boardings":boards[i],"alightings":alights[i]}
                        for i,s in enumerate(station_ids)],"links":links,
            "daily_trips":sum(boards),"balance_residual":sum(boards)-sum(alights)}


def screenline_geH(observed, modelled):
    if observed < 0 or modelled < 0:
        raise ValueError("Negative counts")
    return math.sqrt(2*(modelled-observed)**2/(modelled+observed)) if modelled+observed else 0.0


def appraisal(cashflows, discount_rate, base_year):
    """Annual real economic costs/benefits, separate from fares and financial revenue."""
    if discount_rate <= -1 or not cashflows:
        raise ValueError("Invalid appraisal")
    if len({r['year'] for r in cashflows}) != len(cashflows):
        raise ValueError("Duplicate appraisal years")
    if any(r['benefit'] < 0 or r['cost'] < 0 for r in cashflows):
        raise ValueError("Negative appraisal amounts")
    pb = sum(r['benefit']/(1+discount_rate)**(r['year']-base_year) for r in cashflows)
    pc = sum(r['cost']/(1+discount_rate)**(r['year']-base_year) for r in cashflows)
    return {"present_benefits":pb,"present_costs":pc,"npv":pb-pc,
            "bcr":pb/pc if pc else None,"base_year":base_year,"discount_rate":discount_rate}
