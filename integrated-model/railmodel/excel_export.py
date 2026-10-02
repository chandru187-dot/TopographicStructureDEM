"""Portable Python audit workbook export for GitHub Actions.
Forecast authority remains Engine; Excel formulas mirror selected calculations.
"""
import json,math
from pathlib import Path
import xlsxwriter


def export_workbook(path,runs,data):
 if not runs:raise ValueError('No runs to export')
 selected=runs[0];p=selected['passenger'];f=selected['freight'];Path(path).parent.mkdir(parents=True,exist_ok=True)
 synthetic=data['governance_status'].startswith('SYNTHETIC')
 wb=xlsxwriter.Workbook(str(path),{'strings_to_formulas':False,'strings_to_urls':False})
 wb.set_properties({'title':'Sabah Rail model audit','comments':'Python-authoritative export; draft or synthetic status preserved'})
 title=wb.add_format({'bold':True,'font_size':16,'font_color':'white','bg_color':'#14253B','font_name':'Arial'})
 header=wb.add_format({'bold':True,'font_color':'white','bg_color':'#245C91','text_wrap':True})
 text=wb.add_format({'font_name':'Arial','font_size':10,'text_wrap':True,'valign':'top'})
 num=wb.add_format({'font_name':'Arial','font_size':10,'num_format':'#,##0.00;[Red](#,##0.00);–'})
 linked=wb.add_format({'font_color':'#008000','num_format':'#,##0.00','font_name':'Arial'})
 def write_value(s,row,col,value):
  if value is None:s.write(row,col,'UNRESOLVED',text)
  elif isinstance(value,(dict,list)):s.write_string(row,col,json.dumps(value,ensure_ascii=False),text)
  elif isinstance(value,(int,float))and not isinstance(value,bool):s.write_number(row,col,value,num)
  else:s.write_string(row,col,str(value),text)
 def sheet(name,heads,rows):
  s=wb.add_worksheet(name);s.hide_gridlines(2);s.set_tab_color('#245C91');s.merge_range(0,0,0,max(2,len(heads)-1),name.upper()+' · '+('SYNTHETIC TEST ONLY' if synthetic else 'DRAFT AUDIT'),title);s.set_row(0,30);s.write_row(2,0,heads,header);s.set_row(2,32);s.set_column(0,len(heads)-1,24);s.set_column(0,0,32);s.freeze_panes(3,0)
  for row,vals in enumerate(rows,3):
   lines=1
   for col,v in enumerate(vals):
    value=json.dumps(v,ensure_ascii=False)if isinstance(v,(dict,list))else str(v)
    width=32 if col==0 else 24
    if not isinstance(v,(int,float)):
     lines=max(lines,sum(max(1,math.ceil(len(part)/width))for part in value.split('\n')))
    write_value(s,row,col,v)
   s.set_row(row,min(409,max(30,lines*13+8)))
  if rows:s.autofilter(2,0,len(rows)+2,len(heads)-1)
  return s
 summary=sheet('Summary',['Measure','Value','Interpretation'],[['Status',selected['validation_status'],data['governance_status']],['Passenger trips/day',p['daily_source_trips'],'Source units; interpretation retained'],['Passenger trips/year',p['annual_source_trips'],'Annualisation in Passenger Calc'],['Freight tonnes/year',f['tonnes_year'],'Selected variant/package'],['Freight TEU/year',f['teu_year'],'Includes empty equipment when source does'],['Road VKT avoided',selected['traffic']['net_vkt_avoided'],'Missing data withheld'],['Accidents avoided',selected['safety']['accidents_avoided_year'],'Comparable exposure required'],['Annual economic benefit',selected['economic']['complete_annual_benefit_rm'],'Partial subtotal is not complete benefit'],['Run ID',selected['run_id'],'Immutable input snapshot supplied'],['Model version',selected['model_version'],'Git commit in Run Register'],['Execution environment','GitHub Actions batch / Work test','Actual environment recorded in batch manifest'],['Selected case',selected['scenario']['case'],'First run in comparison; see Scenarios for all cases'],['Forecast year',selected['scenario']['year'],'years'],['Passenger option',selected['scenario'].get('option',3),'Draft option coding; approved alignment crosswalk unresolved']]);summary.set_column(2,2,64)
 controls=sheet('Controls',['Input','Value','Unit / provenance'],[['Daily trips',p['daily_source_trips'],'selected Python source forecast'],['Passenger days',selected['effective_assumptions']['passenger_days'],'days/year; draft source convention'],['Headway',selected['operations']['headway_min'],'minutes'],['Hours',selected['operations']['service_hours'],'hours/day']]);controls.set_column(2,2,50)
 pc=sheet('Passenger Calc',['Measure','Formula mirror','Python reference','Residual'],[['Daily trips',None,p['daily_source_trips'],None],['Annual trips',None,p['annual_source_trips'],None]])
 pc.write_formula('B4','=Controls!B4',linked,p['daily_source_trips']);pc.write_formula('B5','=B4*Controls!B5',num,p['annual_source_trips']);pc.write_formula('D4','=B4-C4',num,0);pc.write_formula('D5','=B5-C5',num,0)
 forecast=[]
 for variant,cases in data['passenger_forecasts'].items():
  for case,opts in cases.items():
   for option,years in opts.items():
    for year,value in years.items():forecast.append([variant,case,int(option),int(year),value,'source trips/day','Draft' if not synthetic else 'Synthetic'])
 sheet('Passenger Forecasts',['Variant','Case','Option','Year','Daily value','Unit','Status'],forecast)
 fm=[]
 for row in f['markets']:
  if 'factors' in row:
   original=next(v for v in data['freight_markets']if v['id']==row['id']);fm.append([row['id'],row['origin'],row['destination'],row['market_volume'],*row['factors'],row['ramp'],original['laden_share'],original['cargo_per_laden_teu'],int(original['container']),None,None,row['tonnes_year'],row['teu_year'],None,row['source_cell']])
 fc=sheet('Freight Calc',['Market','Origin','Destination','Market volume','Development','Rail suit.','Commodity suit.','Terminal','Capture','Ramp','Laden share','t/laden TEU','Container','Excel TEU/year','Excel t/year','Python t/year','Python TEU/year','Residual t','Source cell'],fm)
 for i,row in enumerate(fm,4):
  calc=row[3]*math.prod(row[4:9])*row[9];teu=calc if row[12] else 0;tonnes=teu*row[10]*row[11]if row[12]else calc
  fc.write_formula(i-1,13,f'=IF(M{i}=1,D{i}*E{i}*F{i}*G{i}*H{i}*I{i}*J{i},0)',num,teu)
  fc.write_formula(i-1,14,f'=IF(M{i}=1,N{i}*K{i}*L{i},D{i}*E{i}*F{i}*G{i}*H{i}*I{i}*J{i})',num,tonnes)
  fc.write_formula(i-1,17,f'=O{i}-P{i}',num,tonnes-row[15])
 sheet('Freight OD',['Origin','Destination','Tonnes/year','TEU/year','Status'],[[x['origin'],x['destination'],x['tonnes_year'],x['teu_year'],f['status']]for x in f['od']])
 sheet('Survey QA',['Measure','Value','Status'],[['Passenger raw rows',data['passenger_survey']['raw_rows'],'Unweighted source records'],['Reported sample',data['passenger_survey']['reporting_sample_tn3'],'Source report; sample conflict retained'],['Freight respondents',data['freight_survey']['respondents'],'Indicative OD evidence only']])
 sheet('Passenger Sample OD',['Origin','Destination','Respondents','Status'],[[x['origin'],x['destination'],x['respondents'],'Unweighted sample; not forecast OD']for x in data['passenger_survey']['sample_od']])
 sheet('Traffic Counts',['Site','Direction','Period','Value','Unit','Source locator'],[[x['station'],x['direction'],x['period'],x['value'],x['unit'],x['source_cell']]for x in data['screenline_counts']])
 sheet('Station Catchments',['Name','Daily IN','Daily OUT','Status'],[[x['name'],x['daily_in'],x['daily_out'],x['status']]for x in data['station_catchment_outputs']])
 sheet('Operations',['Measure','Value'],list(selected['operations'].items()))
 sheet('Road and Safety',['Module','Measure','Value'],[[domain,k,v]for domain in ['traffic','safety']for k,v in selected[domain].items()])
 sheet('Economics and Revenue',['Module','Measure','Value'],[[domain,k,v]for domain in ['economic','financial']for k,v in selected[domain].items()])
 scenarios=sheet('Scenarios',['Case','Year','Headway','Passenger/day','Freight t/year','Freight TEU/year','Validation','Run ID'],[[x['scenario']['case'],x['scenario']['year'],x['operations']['headway_min'],x['passenger']['daily_source_trips'],x['freight']['tonnes_year'],x['freight']['teu_year'],x['validation_status'],x['run_id']]for x in runs])
 chart=wb.add_chart({'type':'column'});chart.add_series({'name':'Source passenger trips/day','categories':['Scenarios',3,0,len(runs)+2,0],'values':['Scenarios',3,3,len(runs)+2,3]});chart.set_title({'name':'Selected run comparison · '+('synthetic' if synthetic else 'draft')});chart.set_legend({'none':True});summary.insert_chart('A18',chart,{'x_scale':1.3,'y_scale':1.1})
 sheet('Source Register',['ID','Alias','File','SHA256','Classification','Source date','Approval evidence','Location'],[[x['id'],x.get('aliases'),x['file'],x['sha256'],x['classification'],x.get('source_date'),x.get('approval_evidence'),x.get('path')]for x in data['sources']])
 sheet('Assumptions',['Name','Effective value','Status'],[[k,v,'Draft' if not synthetic else 'Synthetic execution configuration']for k,v in selected['effective_assumptions'].items()])
 sheet('Unresolved',['ID','Domain','Issue','Action','Status'],[[x['id'],x['domain'],x['issue'],x['action'],x['status']]for x in data['unresolved']])
 sheet('Run Register',['Run ID','UTC timestamp','Model','Git SHA','Data SHA256','Scenario','Status'],[[x['run_id'],x['timestamp'],x['model_version'],x['git_version'],x['data_version'],x['scenario'],x['validation_status']]for x in runs])
 sheet('Calculation Trail',['Run ID','Output','Value','Unit','Equation','Sources','Locator','Status'],[[x['run_id'],t['output'],t['value'],t['unit'],t['equation'],t['sources'],t.get('source_cell'),t['status']]for x in runs for t in x['trace']])
 qa=sheet('QA',['Check','Residual / result','Meaning'],[['Passenger daily mirror',0,'Independent formula arithmetic compared'],['Passenger annual mirror',0,'Independent formula arithmetic compared'],['Freight mirror',sum(abs(row[15]-(row[3]*math.prod(row[4:9])*row[9]*(row[10]*row[11]if row[12]else 1)))for row in fm),'Absolute market residual'],['Excel exporter source coverage','NOT APPLICABLE' if not fm else 'PASS','Headline alternative has no disaggregated cargo formulas'],*[[c['check'],c['status'],'Selected Python run validation']for c in selected['checks']]])
 qa.write_formula('B4',"=ABS('Passenger Calc'!D4)",num,0);qa.write_formula('B5',"=ABS('Passenger Calc'!D5)",num,0)
 wb.close();return Path(path)
