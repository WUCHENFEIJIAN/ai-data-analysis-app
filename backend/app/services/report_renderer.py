"""Deterministic, data-only HTML renderer for ReportSpec."""

# The embedded JavaScript/CSS strings are fixed runtime assets.
# ruff: noqa: E501

import base64
import csv
import hashlib
import html
import json
import re
from html.parser import HTMLParser
from typing import Any

from app.core.errors import AppError, ReportPipelineError, ValidationError
from app.schemas.findings import Findings
from app.services.chart_metric_compatibility import shared_axis_display_unit
from app.services.report_semantics import (
    classify_column_values,
    display_label_for,
    format_table_value,
)
from app.services.report_spec import (
    ArtifactImageBlock,
    CalloutBlock,
    ChartBlock,
    KpiGridBlock,
    NarrativeBlock,
    RecommendationBlock,
    ReportSpec,
    ScalarRef,
    SeriesSpec,
    TableBlock,
    VisualGroupBlock,
)
from app.services.workspace import PathResolver

REPORT_DESIGN_TOKENS: dict[str, dict[str, str]] = {
    "editorial": {
        "bg": "#f5f5f3",
        "surface": "#fbfbf9",
        "ink": "#171717",
        "accent": "#272727",
        "muted": "#666666",
        "line": "#d8d8d4",
    }
}

EMBEDDED_ECHARTS_RUNTIME = r"""
/* Deterministic ECharts-compatible SVG runtime for offline reports. */
(function(){
  function esc(v){return String(v==null?'':v).replace(/[&<>\"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]});}
  function num(v){if(v===null||v===undefined||v==='')return null;var n=Number(v);return isFinite(n)?n:null;}
  function fmt(v,s){var raw=num(v);if(raw===null)return '';var scale=num(s&&s.scale)||1,n=raw/scale,d=Number(s&&s.decimals)||0,unit=s&&s.unit||'';if(s&&s.format==='percent'&&unit.indexOf('%')<0)unit+='%';return n.toLocaleString('zh-CN',{minimumFractionDigits:d,maximumFractionDigits:d})+unit;}
  function legend(series,colors){var out='',x=18;series.forEach(function(s,i){out+='<rect x="'+x+'" y="12" width="10" height="10" fill="'+colors[i%colors.length]+'" rx="2"/><text x="'+(x+15)+'" y="22">'+esc(s.name)+'</text>';x+=Math.max(90,String(s.name).length*14+34);});return out;}
  function pie(option,w,h){var s=option.series[0],values=s.data.map(num),total=values.reduce(function(a,b){return a+(b===null?0:b);},0)||1,cx=w/2,cy=h/2+12,r=95,angle=-Math.PI/2,out='';values.forEach(function(v,i){if(v===null)return;var next=angle+v/total*Math.PI*2,x1=cx+r*Math.cos(angle),y1=cy+r*Math.sin(angle),x2=cx+r*Math.cos(next),y2=cy+r*Math.sin(next),large=next-angle>Math.PI?1:0,path='M '+cx+' '+cy+' L '+x1+' '+y1+' A '+r+' '+r+' 0 '+large+' 1 '+x2+' '+y2+' Z';out+='<path d="'+path+'" fill="'+option.color[i%option.color.length]+'"><title>'+esc(option.labels[i])+': '+esc(fmt(v,s))+'</title></path>';angle=next;});if(option.chartType==='donut')out+='<circle cx="'+cx+'" cy="'+cy+'" r="50" fill="'+esc(option.surface)+'"/>';return out;}
  function estimateTextWidth(text){return Math.max(10, String(text).length*7.4);}
  function parseTime(value){
    var s=String(value==null?'':value).trim();
    var m=s.match(/^(\d{4})(?:[-/](\d{1,2})(?:[-/](\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::\d{2})?)?)?)?/);
    if(!m) return null;
    return {y:+m[1], mo:+(m[2]||1), d:+(m[3]||1), hasTime:!!m[4], precision:m[2]?(m[3]?"day":"month"):"year"};
  }
  function analyzeTimes(labels){
    var parsed=labels.map(parseTime);
    if(!parsed.length || parsed.some(function(p){return !p;})) return null;
    var first=parsed[0], last=parsed[parsed.length-1];
    var spanDays=(Date.UTC(last.y,last.mo-1,last.d)-Date.UTC(first.y,first.mo-1,first.d))/86400000;
    var crossesYear=parsed.some(function(p){return p.y!==first.y;});
    return {parsed:parsed, spanDays:spanDays, crossesYear:crossesYear};
  }
  function formatTemporalTick(p, info){
    if(p.precision==="month") return info.crossesYear?p.y+"/"+p.mo:String(p.mo);
    if(p.precision==="year") return String(p.y);
    if(info.crossesYear) return p.y+"/"+p.mo;
    return p.mo+"/"+p.d;
  }
  function buildXAxisTicks(labels, plotW, semantic){
    var n=labels.length;
    if(n===0) return [];
    var info=(semantic==='date'||semantic==='datetime'||semantic==='month'||semantic==='year'||semantic==='temporal')?analyzeTimes(labels):null;
    var texts=labels.map(function(label,i){
      if(info) return formatTemporalTick(info.parsed[i], info);
      return String(label==null?'':label);
    });
    function pack(indices){
      return indices.map(function(i){return {index:i, text:texts[i], raw:String(labels[i]==null?'':labels[i])};});
    }
    if(n===1) return pack([0]);
    var widths=texts.map(estimateTextWidth);
    var gap=14;
    function fits(indices){
      for(var k=1;k<indices.length;k++){
        var prev=indices[k-1], cur=indices[k];
        var prevX=plotW*(prev+0.5)/n, curX=plotW*(cur+0.5)/n;
        if(curX-prevX<(widths[prev]+widths[cur])/2+gap) return false;
      }
      return true;
    }
    var all=[]; for(var i=0;i<n;i++) all.push(i);
    if(fits(all)) return pack(all);
    var avg=widths.reduce(function(a,b){return a+b;},0)/Math.max(1,n);
    var capacity=Math.max(2, Math.min(10, Math.floor(plotW/(avg+gap))));
    for(var count=capacity; count>=2; count--){
      var indices=[0], inner=count-2;
      if(inner>0){
        for(var k=1;k<=inner;k++){
          var idx=Math.round(k*(n-1)/(inner+1));
          if(idx<=0) idx=1;
          if(idx>=n-1) idx=n-2;
          if(indices.indexOf(idx)<0) indices.push(idx);
        }
      }
      indices.push(n-1);
      indices.sort(function(a,b){return a-b;});
      indices=indices.filter(function(v,i,arr){return arr.indexOf(v)===i;});
      if(fits(indices)||count===2) return pack(indices);
    }
    return pack([0,n-1]);
  }
  function effectiveShowLabels(option, plotW){
    var n=option.labels.length, type=option.chartType, preferred=!!option.showLabels;
    if(type==='line'||type==='area'){
      if(n>8||n*36>plotW) return false;
      return preferred;
    }
    if(type==='scatter'){
      if(n>16) return false;
      return preferred;
    }
    if(type==='bar'||type==='grouped_bar'||type==='stacked_bar'||type==='horizontal_bar'){
      if(n>12) return false;
      return preferred;
    }
    return preferred;
  }
  function chartHeight(option){
    var n=option.labels.length, seriesN=option.series.length, type=option.chartType;
    if(type==='horizontal_bar') return Math.max(220, 56+n*32+(seriesN>1?24:0));
    var h=300;
    if(type==='bar'||type==='grouped_bar'||type==='stacked_bar') h=n>8?340:300;
    if(seriesN>=3) h+=24;
    return h;
  }
  function horizontal(option,w,h){
    var labels=option.labels,series=option.series,left=Math.min(220, Math.max(145, Math.max.apply(null, labels.map(function(l){return String(l).length;}))*8)),right=86,top=48,row=Math.max(30,(h-top-20)/Math.max(1,labels.length)),max=1,out='',showLabels=effectiveShowLabels(option, w-left-right);
    series.forEach(function(s){s.data.forEach(function(v){var n=num(v);if(n!==null)max=Math.max(max,n);});});
    out+='<line x1="'+left+'" y1="'+(top-10)+'" x2="'+left+'" y2="'+(h-12)+'" stroke="currentColor" opacity=".22"/>';
    labels.forEach(function(label,i){
      var y=top+i*row+row/2;
      out+='<text x="'+(left-10)+'" y="'+(y+4)+'" text-anchor="end">'+esc(label)+'</text>';
      series.forEach(function(s,si){
        var v=num(s.data[i]);if(v===null)return;var bh=Math.max(7,row/(series.length+1)-3),yy=y-row*.28+si*(bh+3),bw=v/max*(w-left-right);
        if(v!==0)bw=Math.max(3,bw);
        out+='<rect x="'+left+'" y="'+yy+'" width="'+bw+'" height="'+bh+'" fill="'+option.color[si%option.color.length]+'" rx="3"><title>'+esc(s.name)+': '+esc(fmt(v,s))+'</title></rect>';
        if(showLabels)out+='<text class="value-label" x="'+(left+bw+6)+'" y="'+(yy+bh-1)+'">'+esc(fmt(v,s))+'</text>';
      });
    });
    return out;
  }
  function niceScale(raw){
    if(!isFinite(raw)||raw<=0)return {max:1,step:.2,intervals:5};
    var multipliers=[1,2,2.5,5,10],power=Math.pow(10,Math.floor(Math.log(raw/5)/Math.LN10)),best=null;
    for(var pi=-1;pi<=1;pi++)for(var mi=0;mi<multipliers.length;mi++){
      var step=multipliers[mi]*power*Math.pow(10,pi),intervals=Math.ceil(raw/step),max=intervals*step;
      if(Math.abs(max-raw)<=Math.abs(raw)*1e-12){intervals+=1;max=intervals*step;}
      if(intervals<4||intervals>6)continue;
      var score=(max-raw)/raw+Math.abs(intervals-5)*.03;
      if(!best||score<best.score)best={max:max,step:step,intervals:intervals,score:score};
    }
    if(!best){var fallback=Math.pow(10,Math.floor(Math.log(raw)/Math.LN10));best={step:fallback,max:Math.ceil(raw/fallback)*fallback,intervals:Math.ceil(raw/fallback)};}
    return best;
  }
  function yAxisTickWidth(scale, seriesSpec){
    var maxW=0;
    for(var ti=0;ti<=scale.intervals;ti++){
      maxW=Math.max(maxW, estimateTextWidth(fmt(scale.step*ti, seriesSpec)));
    }
    return maxW;
  }
  function yAxisPadding(scale, seriesSpec, side){
    var minPad=side==='right'?28:36;
    var tickGap=side==='right'?14:12;
    return Math.min(220, Math.max(minPad, yAxisTickWidth(scale, seriesSpec)+tickGap));
  }
  function axisTick(axis,value,y,x,anchor){return '<text class="axis-tick" data-axis="'+axis+'" data-raw-value="'+value+'" data-scale="'+(x.scale||1)+'" data-unit="'+esc(x.unit||'')+'" x="'+anchor.x+'" y="'+(y+4)+'" text-anchor="'+anchor.align+'">'+esc(fmt(value,x))+'</text>';}
  function xPosition(labels,index,plotW,semantic){
    var temporal=(semantic==='date'||semantic==='datetime'||semantic==='month'||semantic==='year'||semantic==='temporal')?analyzeTimes(labels):null;
    if(!temporal || temporal.parsed.length<2) return plotW*(index+.5)/Math.max(1,labels.length);
    var first=temporal.parsed[0], last=temporal.parsed[temporal.parsed.length-1];
    var min=Date.UTC(first.y,first.mo-1,first.d), max=Date.UTC(last.y,last.mo-1,last.d), current=Date.UTC(temporal.parsed[index].y,temporal.parsed[index].mo-1,temporal.parsed[index].d);
    return (current-min)/Math.max(1,max-min)*plotW;
  }

  function cartesian(option,w,h){
    var labels=option.labels,series=option.series,hasRight=series.some(function(s){return s.axis==='right';}),top=44,bottom=48,base=h-bottom,plotH=base-top,rawMax={left:0,right:0},out='';
    series.forEach(function(s){var axis=s.axis==='right'?'right':'left';s.data.forEach(function(v){var n=num(v);if(n!==null)rawMax[axis]=Math.max(rawMax[axis],n);});});
    var scales={left:niceScale(rawMax.left),right:niceScale(rawMax.right)},axisSeries={left:series.filter(function(s){return s.axis!=='right';})[0]||{},right:series.filter(function(s){return s.axis==='right';})[0]||{}};
    var left=yAxisPadding(scales.left,axisSeries.left,'left'),right=hasRight?yAxisPadding(scales.right,axisSeries.right,'right'):22,plotW=Math.max(80,w-left-right),showLabels=effectiveShowLabels(option, plotW),barOnly=['bar','grouped_bar','stacked_bar'].indexOf(option.chartType)>=0,needsAxis=hasRight||!barOnly||!showLabels;
    out+='<line x1="'+left+'" y1="'+base+'" x2="'+(w-right)+'" y2="'+base+'" stroke="currentColor" opacity=".28"/>';
    if(needsAxis){for(var ti=0;ti<=scales.left.intervals;ti++){var tick=scales.left.step*ti,y=base-tick/scales.left.max*plotH;out+='<line x1="'+left+'" y1="'+y+'" x2="'+(w-right)+'" y2="'+y+'" stroke="currentColor" opacity="'+(ti===0?'.18':'.10')+'"/>'+axisTick('left',tick,y,axisSeries.left,{x:left-9,align:'end'});}}
    if(hasRight&&needsAxis){for(var ri=0;ri<=scales.right.intervals;ri++){var rt=scales.right.step*ri,ry=base-rt/scales.right.max*plotH;out+=axisTick('right',rt,ry,axisSeries.right,{x:w-right+9,align:'start'});}}
    buildXAxisTicks(labels, plotW, option.xSemantic||'category').forEach(function(tick){
      var x=left+xPosition(labels,tick.index,plotW,option.xSemantic||"category");
      out+='<text class="category-label" data-tick-index="'+tick.index+'" data-raw-label="'+esc(tick.raw)+'" x="'+x+'" y="'+(h-18)+'" text-anchor="middle">'+esc(tick.text)+'</text>';
    });
    series.forEach(function(s,si){
      var type=s.type||'bar',color=option.color[si%option.color.length],scaleMax=scales[s.axis==='right'?'right':'left'].max;
      if(['line','area','scatter'].indexOf(type)>=0){
        var pts=[];
        s.data.forEach(function(v,i){
          var n=num(v);if(n===null)return;var x=left+xPosition(labels,i,plotW,option.xSemantic||"category"),y=base-n/scaleMax*plotH;
          pts.push(x+','+y);
          if(showLabels)out+='<text class="value-label" x="'+x+'" y="'+(y-8)+'" text-anchor="middle">'+esc(fmt(n,s))+'</text>';
          out+='<circle class="data-point" data-point-index="'+i+'" cx="'+x+'" cy="'+y+'" r="3.5" fill="'+color+'"><title>'+esc(s.name)+': '+esc(fmt(n,s))+'</title></circle>';
        });
        if(type==='area')out+='<polygon points="'+pts.join(' ')+' '+(left+plotW*(labels.length-.5)/Math.max(1,labels.length))+','+base+' '+(left+plotW*.5/Math.max(1,labels.length))+','+base+'" fill="'+color+'" opacity=".13"/>';
        if(type!=='scatter')out+='<polyline points="'+pts.join(' ')+'" fill="none" stroke="'+color+'" stroke-width="2.5"/>';
      }else{
        var group=series.filter(function(x){return (x.type||'bar')==='bar';}),gi=group.indexOf(s),slot=plotW/Math.max(1,labels.length),bw=Math.max(6,Math.min(42,slot*.68/Math.max(1,group.length)));
        s.data.forEach(function(v,i){
          var n=num(v);if(n===null)return;var x=left+slot*i+slot*.5+(gi-(group.length-1)/2)*bw,y=base-n/scaleMax*plotH,bh=base-y;
          if(n!==0)bh=Math.max(3,bh);
          out+='<rect x="'+(x-bw/2+1)+'" y="'+y+'" width="'+(bw-2)+'" height="'+bh+'" fill="'+color+'" rx="3"><title>'+esc(s.name)+': '+esc(fmt(n,s))+'</title></rect>';
          if(showLabels)out+='<text class="value-label" x="'+x+'" y="'+(y-7)+'" text-anchor="middle">'+esc(fmt(n,s))+'</text>';
        });
      }
    });
    return out;
  }
  function init(el){return {setOption:function(option){var w=960,h=chartHeight(option),svg='<svg class="echarts-svg" viewBox="0 0 '+w+' '+h+'" role="img" aria-label="'+esc(option.title.text)+'">';if(option.showLegend&&option.series.length>1)svg+=legend(option.series,option.color);if(option.chartType==='pie'||option.chartType==='donut')svg+=pie(option,w,h);else if(option.chartType==='horizontal_bar')svg+=horizontal(option,w,h);else svg+=cartesian(option,w,h);svg+='</svg>';el.innerHTML=svg;}};}
  window.echarts={version:'5.5.0-offline',init:init};
})();
"""


def _require_reference(
    registry: dict[str, Any],
    reference_id: str,
    *,
    section_id: str,
    block_type: str,
    reference_type: str,
) -> Any:
    try:
        return registry[reference_id]
    except KeyError as exc:
        raise ReportPipelineError(
            "report_reference_invalid",
            details={
                "section": section_id,
                "block": block_type,
                "reference_type": reference_type,
                "reference_id": reference_id,
            },
        ) from exc


class ReportRenderer:
    def __init__(self, resolver: PathResolver) -> None:
        self.resolver = resolver

    def render(self, project_id: str, spec: ReportSpec) -> str:
        findings = self._findings(project_id)
        sources = {item.id: item for item in spec.sources}
        kpis = {item.id: item for item in spec.kpis}
        composites = {
            item.id: item for item in (spec.storyline.composite_insights if spec.storyline else [])
        }
        theme = REPORT_DESIGN_TOKENS[spec.theme]
        self._active_theme = theme
        parts = [
            "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>",
            "<meta name='viewport' content='width=device-width,initial-scale=1'>",
            f"<title>{html.escape(spec.title)}</title>",
            "<style>",
            self._css(theme),
            "</style></head><body><div class='report-container'>",
            f"<header><p class='eyebrow reading-measure'>{html.escape(spec.analysis_topic)}</p>"
            f"<h1 class='reading-measure'>{html.escape(spec.title)}</h1>",
            f"<p class='subtitle reading-measure'>{html.escape(spec.subtitle or '')}</p>",
            f"<p class='summary reading-measure'><strong>核心摘要：</strong>"
            f"{html.escape(spec.executive_summary or findings.summary)}</p></header><main>",
        ]
        chart_options: list[tuple[str, dict[str, Any]]] = []
        for section in spec.sections:
            classes = f"section layout-{section.layout}"
            parts.append(
                f"<section class='{classes}' data-narrative-role='{html.escape(section.narrative_role)}' "
                f"data-visual-strategy='{html.escape(section.visual_strategy)}' "
                f"data-editorial-priority='{section.priority}'>"
                f"<h2 class='reading-measure'>{html.escape(section.title)}</h2>"
                "<div class='blocks'>"
            )
            for block in section.blocks:
                rendered, options = self._block(
                    project_id, block, sources, findings, kpis, composites, section.id
                )
                parts.append(rendered)
                chart_options.extend(options)
            parts.append("</div></section>")
        parts.extend(
            [
                "</main><footer>报告由结构化证据生成 · 所有数据来源见各区块注释</footer></div>",
                "<script>",
                EMBEDDED_ECHARTS_RUNTIME,
                "\n",
            ]
        )
        for container_id, option in chart_options:
            serialized_option = (
                json.dumps(option, ensure_ascii=False, separators=(",", ":"))
                .replace("<", "\\u003c")
                .replace(">", "\\u003e")
                .replace("&", "\\u0026")
            )
            parts.append(
                f"echarts.init(document.getElementById('{container_id}')).setOption({serialized_option});"
            )
        parts.extend(["</script></body></html>"])
        html_text = "".join(parts)
        self.validate_html(html_text)
        return html_text

    def _block(
        self,
        project_id: str,
        block: Any,
        sources: dict[str, Any],
        findings: Findings,
        kpis: dict[str, Any],
        composites: dict[str, Any],
        section_id: str = "",
    ) -> tuple[str, list[tuple[str, dict[str, Any]]]]:
        if isinstance(block, KpiGridBlock):
            cards = []
            for kpi_id in block.kpi_ids:
                kpi = kpis[kpi_id]
                if kpi.metric_definition is not None:
                    value = kpi.metric_definition.value
                elif kpi.value_ref is not None:
                    value = self._scalar(project_id, kpi.value_ref, sources)
                else:
                    raise ValidationError(f"KPI {kpi.id} has no metric value")
                count_note = ""
                if (
                    kpi.metric_definition is not None
                    and kpi.metric_definition.count_semantics == "field_sum"
                ):
                    count_note = (
                        "<p class='metric-note'>注：该指标为源数据计数字段合计，"
                        "不代表实体去重数。</p>"
                    )
                definition = (
                    "<details class='metric-definition'><summary>口径说明</summary>"
                    f"<p>{html.escape(kpi.definition_note)}</p></details>"
                    if kpi.definition_note
                    else ""
                )
                cards.append(
                    f"<article class='kpi' data-finding-ids='{html.escape(','.join(kpi.finding_ids))}' "
                    f"data-claim-ids='{html.escape(','.join(kpi.supports_claim_ids))}' "
                    f"data-kpi-role='{html.escape(block.presentation_role)}' "
                    f"data-kpi-roles='{html.escape(','.join(kpi.presentation_roles))}'>"
                    f"<p class='kpi-label'>{html.escape(kpi.display_label or kpi.label)}</p>"
                    f"<strong class='kpi-value'>{html.escape(self._format(value, kpi.format, kpi.decimals, kpi.scale))}"
                    f"{html.escape(kpi.unit or '')}</strong>"
                    f"<p class='kpi-purpose'>{html.escape(kpi.purpose)}</p>"
                    f"{count_note}{definition}</article>"
                )
            return "<div class='kpi-grid wide-visual'>" + "".join(cards) + "</div>", []
        if isinstance(block, ChartBlock):
            option = self._chart_option(project_id, block.chart, sources[block.chart.source_id])
            container_id = (
                "chart_"
                + hashlib.sha1(
                    json.dumps(option, sort_keys=True, ensure_ascii=False).encode("utf-8")
                ).hexdigest()[:12]
            )
            option["title"] = {"text": block.chart.title}
            subtitle = (
                f"<p class='chart-subtitle'>{html.escape(block.chart.subtitle)}</p>"
                if block.chart.subtitle
                else ""
            )
            return (
                f"<article class='chart-card wide-visual' data-finding-ids='{html.escape(','.join(block.chart.finding_ids))}' "
                f"data-claim-ids='{html.escape(','.join(block.chart.supports_claim_ids))}' "
                f"data-visual-purpose='{html.escape(block.chart.visual_purpose)}' "
                f"data-visual-priority='{block.chart.visual_priority}' "
                f"data-source-artifact='{html.escape(sources[block.chart.source_id].artifact_path)}'>"
                f"<h3>{html.escape(block.chart.title)}</h3>{subtitle}"
                f"<div id='{container_id}' class='echarts-container'></div>"
                f"<p class='source'>{html.escape(self._source_caption(block.chart.source_caption))}</p></article>",
                [(container_id, option)],
            )
        if isinstance(block, TableBlock):
            source = sources[block.source_id]
            records = self._records(project_id, source, block.records_path)
            visible = records[: block.row_limit]
            omitted = len(records) - len(visible)
            head = "".join(
                f"<th data-field='{html.escape(column.field)}'>{html.escape(_column_display_label(column))}</th>"
                for column in block.columns
            )
            semantics = {
                column.field: _resolved_column_semantic(
                    column, [row.get(column.field) for row in visible]
                )
                for column in block.columns
            }
            rows = "".join(
                "<tr>"
                + "".join(
                    f"<td>{html.escape(self._table_value(row.get(column.field, ''), column, semantics[column.field]))}</td>"
                    for column in block.columns
                )
                + "</tr>"
                for row in visible
            )
            omission = "<p class='table-omission'>其余内容未在正文展开</p>" if omitted > 0 else ""
            return (
                f"<article class='table-card wide-visual{' table-appendix' if block.usage == 'appendix' else ''}' "
                f"data-table-usage='{html.escape(block.usage)}' "
                f"data-finding-ids='{html.escape(','.join(block.finding_ids))}' "
                f"data-claim-ids='{html.escape(','.join(block.supports_claim_ids))}' "
                f"data-visual-priority='{block.visual_priority}' "
                f"data-source-artifact='{html.escape(source.artifact_path)}'>"
                f"<h3>{html.escape(block.title)}</h3>"
                f"<div class='table-scroll'><table><thead><tr>{head}</tr></thead>"
                f"<tbody>{rows}</tbody></table></div>{omission}"
                f"<p class='source'>数据来源：分析阶段生成的结构化数据</p></article>",
                [],
            )
        if isinstance(block, ArtifactImageBlock):
            source = sources[block.source_id]
            data_uri = self._image_data_uri(project_id, source)
            caption = (
                f"<figcaption>{html.escape(self._source_caption(block.caption))}</figcaption>"
                if block.caption
                else ""
            )
            return (
                f"<figure class='artifact-image wide-visual' data-finding-ids='{html.escape(','.join(block.finding_ids))}' "
                f"data-claim-ids='{html.escape(','.join(block.supports_claim_ids))}' "
                f"data-visual-purpose='{html.escape(block.visual_purpose)}' "
                f"data-visual-priority='{block.visual_priority}' "
                f"data-source-artifact='{html.escape(source.artifact_path)}'>"
                f"<img src='{data_uri}' alt='{html.escape(block.alt)}'>{caption}</figure>",
                [],
            )
        if isinstance(block, CalloutBlock):
            title = f"<h3>{html.escape(block.title)}</h3>" if block.title else ""
            return (
                f"<aside class='callout reading-measure callout-{html.escape(block.tone)}'>{title}"
                f"<p>{html.escape(block.text)}</p></aside>",
                [],
            )
        if isinstance(block, NarrativeBlock):
            claims = {
                claim.claim_id: claim for finding in findings.findings for claim in finding.claims
            }
            for claim_id in block.claim_ids:
                _require_reference(
                    claims,
                    claim_id,
                    section_id=section_id,
                    block_type="narrative",
                    reference_type="claim_id",
                )
            for insight_id in block.composite_insight_ids:
                _require_reference(
                    composites,
                    insight_id,
                    section_id=section_id,
                    block_type="narrative",
                    reference_type="composite_insight_id",
                )
            paragraphs = [
                part.strip() for part in re.split(r"\n+", block.text or "") if part.strip()
            ]
            role = block.display_role.replace("_", "-")
            related = block.related_block_id or ""
            return (
                f"<article class='editorial-narrative reading-measure narrative-{html.escape(role)}' "
                f"data-display-role='{html.escape(block.display_role)}' "
                f"data-claim-ids='{html.escape(','.join(block.claim_ids))}' "
                f"data-related-block='{html.escape(related)}'>"
                + "".join(f"<p>{html.escape(statement)}</p>" for statement in paragraphs)
                + "</article>",
                [],
            )
        if isinstance(block, RecommendationBlock):
            labels = {
                "immediate": "立即行动",
                "near_term": "近期推进",
                "monitor": "持续监测",
            }
            groups = []
            for priority in ("immediate", "near_term", "monitor"):
                items = [item for item in block.items if item.priority == priority]
                if not items:
                    continue
                item_markup = "".join(
                    f"<li data-finding-ids='{html.escape(','.join(item.source_finding_ids))}' "
                    f"data-claim-ids='{html.escape(','.join(item.source_claim_ids))}'>"
                    f"{html.escape(item.text)}</li>"
                    for item in items
                )
                if len(items) == 1:
                    item = items[0]
                    item_markup = (
                        f"<p class='recommendation-single' "
                        f"data-finding-ids='{html.escape(','.join(item.source_finding_ids))}' "
                        f"data-claim-ids='{html.escape(','.join(item.source_claim_ids))}'>"
                        f"{html.escape(item.text)}</p>"
                    )
                else:
                    item_markup = f"<ol>{item_markup}</ol>"
                groups.append(
                    f"<section class='recommendation-group' data-priority='{priority}'>"
                    f"<h3>{labels[priority]}</h3>{item_markup}</section>"
                )
            return "<div class='recommendation-groups'>" + "".join(groups) + "</div>", []
        if isinstance(block, VisualGroupBlock):
            item_html: list[str] = []
            options: list[tuple[str, dict[str, Any]]] = []
            for item in block.items:
                rendered, item_options = self._block(
                    project_id, item, sources, findings, kpis, composites, section_id
                )
                item_html.append(rendered)
                options.extend(item_options)
            return (
                f"<div class='visual-group visual-group-{html.escape(block.layout)} wide-visual'>"
                + "".join(item_html)
                + "</div>",
                options,
            )
        raise ValidationError("Unsupported report block")

    @staticmethod
    def _source_caption(caption: str) -> str:
        """Keep internal artifact paths in metadata, not visible report prose."""

        if "/" in caption or "\\" in caption:
            return "数据来源：分析阶段生成的结构化数据"
        return caption

    def _chart_option(self, project_id: str, chart: Any, source: Any) -> dict[str, Any]:
        records = self._records(project_id, source, chart.records_path)
        if chart.sort_by and chart.sort_order != "source":
            records = sorted(
                records,
                key=lambda row: _sort_value(row.get(chart.sort_by)),
                reverse=chart.sort_order == "desc",
            )
        records = records[: chart.row_limit]
        labels = [str(row.get(chart.x_field, "")) for row in records]
        series = []
        axes: dict[str, dict[str, Any]] = {}
        default_series_type = {
            "line": "line",
            "area": "area",
            "scatter": "scatter",
            "pie": "pie",
            "donut": "donut",
        }.get(chart.chart_type, "bar")
        series_by_axis: dict[str, list[SeriesSpec]] = {}
        for spec in chart.series:
            series_by_axis.setdefault(spec.axis, []).append(spec)
        for spec in chart.series:
            values = [_number_or_value(row.get(spec.field)) for row in records]
            series.append(
                {
                    "name": spec.label,
                    "type": spec.visual_type or default_series_type,
                    "data": values,
                    "metric": spec.metric,
                    "format": spec.format,
                    "decimals": spec.decimals,
                    "unit": spec.unit or "",
                    "scale": spec.scale,
                    "axis": spec.axis,
                }
            )
            axes.setdefault(
                spec.axis,
                {
                    "id": spec.axis,
                    "format": spec.format,
                    "decimals": spec.decimals,
                    "unit": shared_axis_display_unit(series_by_axis[spec.axis]),
                    "scale": spec.scale,
                    "unitFamily": (
                        spec.metric_definition.unit_family
                        if spec.metric_definition is not None
                        else ""
                    ),
                },
            )
        palette = getattr(self, "_active_theme", REPORT_DESIGN_TOKENS["editorial"])
        return {
            "animation": False,
            "color": [palette["accent"], "#557c77", "#d59b62", "#77726d"],
            "chartType": chart.chart_type,
            "labels": labels,
            "xSemantic": (
                getattr(chart, "x_semantic", "category")
                if getattr(chart, "x_semantic", "category") != "category"
                else _x_semantic(labels)
            ),
            "surface": palette["surface"],
            "showLegend": chart.show_legend,
            "showLabels": chart.show_labels,
            "axes": list(axes.values()),
            "series": series,
        }

    def _scalar(self, project_id: str, ref: ScalarRef, sources: dict[str, Any]) -> Any:
        source = sources[ref.source_id]
        selector = ref.selector
        if hasattr(selector, "path"):
            return self._read_value(project_id, source, selector.path)
        records = self._records(project_id, source, selector.records_path)
        return records[selector.row].get(selector.field)

    @staticmethod
    def _format(value: Any, format_name: str, decimals: int, scale: int = 1) -> str:
        if format_name == "integer":
            try:
                return f"{int(float(str(value).replace(',', '').rstrip('%')) / scale):,}"
            except (TypeError, ValueError):
                return str(value)
        try:
            number = float(str(value).replace(",", "").rstrip("%"))
        except (TypeError, ValueError):
            return str(value)
        rendered = f"{number / scale:,.{decimals}f}"
        return rendered + ("%" if format_name == "percent" else "")

    def _table_value(self, value: Any, column: Any, semantic: str | None = None) -> str:
        resolved = semantic or getattr(column, "semantic_type", None) or "text"
        if resolved != "text" or column.format != "text":
            return format_table_value(
                value,
                resolved,
                format_name=column.format,
                decimals=column.decimals,
                unit=column.unit,
                scale=column.scale,
            )
        if value in (None, ""):
            return "" if value is None else str(value)
        return str(value)

    def _records(self, project_id: str, source: Any, path: list[str | int]) -> list[dict[str, Any]]:
        value = self._read_value(project_id, source, path)
        if isinstance(value, dict):
            value = value.get("records", value.get("data", value))
        if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
            raise ValidationError("Source selector does not resolve to tabular records")
        return value

    def _read_value(self, project_id: str, source: Any, path: list[str | int]) -> Any:
        resolved = self.resolver.resolve(project_id, source.artifact_path)
        if resolved.suffix.lower() == ".csv":
            with resolved.open("r", encoding="utf-8-sig", newline="") as handle:
                value: Any = list(csv.DictReader(handle))
        else:
            try:
                value = json.loads(resolved.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValidationError("Source JSON cannot be read") from exc
        for part in path:
            if isinstance(value, list) and isinstance(part, int) and 0 <= part < len(value):
                value = value[part]
            elif isinstance(value, dict) and isinstance(part, str) and part in value:
                value = value[part]
            else:
                raise ValidationError("Source selector path is invalid")
        return value

    def _findings(self, project_id: str) -> Findings:
        path = self.resolver.resolve(project_id, "analysis/findings.json")
        return Findings.model_validate_json(path.read_text(encoding="utf-8"))

    def _image_data_uri(self, project_id: str, source: Any) -> str:
        path = self.resolver.resolve(project_id, source.artifact_path)
        raw = path.read_bytes()
        if path.suffix.lower() == ".svg":
            text = raw.decode("utf-8", errors="strict")
            cleaned = re.sub(
                r"\s+xmlns(?::[A-Za-z0-9_-]+)?\s*=\s*['\"]https?://[^'\"]+['\"]",
                "",
                text,
                flags=re.I,
            )
            if re.search(r"<script|on[a-z]+\s*=|(?:https?:|//)", cleaned, re.I):
                raise AppError("unsafe_report_asset", "SVG asset failed security validation", 422)
            raw = cleaned.encode("utf-8")
        media = "image/svg+xml" if path.suffix.lower() == ".svg" else source.media_type
        return f"data:{media};base64,{base64.b64encode(raw).decode('ascii')}"

    @staticmethod
    def _css(theme: dict[str, str]) -> str:
        bg, ink, accent, muted, line = (
            theme["bg"],
            theme["ink"],
            theme["accent"],
            theme["muted"],
            theme["line"],
        )
        return f"""
:root{{--content-width:960px;--space-section:40px;--space-block:28px;--space-text:12px;--space-visual:18px}}
*{{box-sizing:border-box}}html,body{{margin:0;background:{bg}}}
body{{color:{ink};font-family:Arial,'Microsoft YaHei','Noto Sans SC',sans-serif;line-height:1.65}}
.report-container{{width:100%;max-width:var(--content-width);margin:0 auto;padding:40px 48px}}
h1,h2,h3,p{{letter-spacing:0;overflow-wrap:anywhere}}
.reading-measure{{width:100%}}
.wide-visual{{width:100%}}
header{{border-top:4px solid {accent};border-bottom:1px solid {line};padding:22px 0 28px}}
.eyebrow{{margin:0;font-size:14px;font-weight:700;color:{accent}}}
h1{{font:700 36px 'Microsoft YaHei',Arial,sans-serif;line-height:1.25;margin:6px 0 10px;color:{ink}}}
.subtitle{{margin:0;color:{muted};font-size:16px}}
.summary{{margin:20px 0 0;padding:0 0 0 18px;border-left:3px solid {accent};font-size:17px}}
.section{{padding:var(--space-section) 0}}
.section+.section{{border-top:1px solid {line}}}
.section>h2{{font:700 23px 'Microsoft YaHei',Arial,sans-serif;line-height:1.35;margin:0 0 var(--space-block)}}
.blocks{{display:flex;flex-direction:column;gap:var(--space-block);align-items:stretch}}
.layout-two-column .blocks,.layout-visual-focus .blocks{{display:flex;flex-direction:column}}
.layout-visual-focus .blocks{{gap:calc(var(--space-block) + 6px)}}
.editorial-narrative p{{font-size:17px;margin:0}}.narrative-lead p{{font-size:18px;font-weight:600}}.narrative-limitation p{{color:{muted};font-size:15px}}.table-appendix h3{{color:{muted}}}
.editorial-narrative p+p{{margin-top:var(--space-text)}}
.kpi-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px}}
.kpi{{background:transparent;border-top:2px solid {accent};border-bottom:1px solid {line};padding:14px 0;margin:0}}
.kpi-label{{font-size:14px;color:{muted};margin:0 0 4px}}
.kpi-value,.numeric{{font-family:Arial,'Segoe UI','Microsoft YaHei',sans-serif;font-variant-numeric:tabular-nums;font-feature-settings:'tnum' 1;font-size:28px}}
.kpi-purpose,.purpose,.chart-subtitle{{font-size:14px;color:{muted};margin:7px 0 0}}
.metric-note{{font-size:12px;color:{muted};margin:8px 0 0}}
.metric-definition{{font-size:12px;color:{muted};margin-top:8px}}
.metric-definition summary{{cursor:pointer;font-weight:700}}.metric-definition p{{margin:5px 0 0}}
.recommendation-groups{{display:flex;flex-direction:column;gap:var(--space-block);align-items:stretch}}
.recommendation-group{{background:transparent;border:none;border-top:1px solid {line};padding:var(--space-block) 0;margin:0}}
.recommendation-group h3{{font-size:16px;margin:0 0 10px}}.recommendation-group ol{{margin:0;padding-left:22px}}
.recommendation-single{{margin:0}}
.recommendation-group li+li{{margin-top:8px}}
.chart-card,.table-card,.artifact-image{{background:transparent;border-top:1px solid {line};border-bottom:1px solid {line};padding:var(--space-visual) 0;margin:0}}
.chart-card h3,.table-card h3{{font-size:17px;margin:0}}
.callout{{padding:14px 18px;border-left:3px solid}}.callout h3{{font-size:15px;margin:0 0 5px}}
.callout p{{margin:0}}.callout-risk{{border-color:#777;background:#ededeb}}.callout-insight{{border-color:#444;background:#f0f0ed}}
.callout-note{{border-color:#999;background:#f3f3f1}}
.echarts-container{{width:100%;margin-top:var(--space-text);line-height:0}}
.echarts-svg{{width:100%;height:auto;display:block;color:{muted};font:14px Arial,'Microsoft YaHei',sans-serif}}
.echarts-svg text{{fill:currentColor}}.echarts-svg .value-label{{font-size:12px;font-weight:700}}
.source{{color:{muted};font-size:13px;margin:6px 0 0}}footer,figcaption,.table-omission{{color:{muted};font-size:14px;margin:10px 0 0}}
.artifact-image img{{display:block;max-width:100%;height:auto;margin:14px auto 0}}
.table-scroll{{overflow-x:auto;margin-top:var(--space-text)}}table{{border-collapse:collapse;width:100%;font-size:14px}}
th,td{{border-bottom:1px solid {line};padding:9px;text-align:left;white-space:nowrap}}
th{{background:{bg}}}tbody tr:nth-child(even){{background:{bg}}}
footer{{padding:20px 0 0;border-top:1px solid {line}}}
.visual-group,.visual-group-stack{{display:flex;flex-direction:column;gap:var(--space-block)}}
.visual-group-two-column{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:var(--space-block)}}
@media print{{.report-container{{padding:20px}}.section{{break-inside:avoid}}}}
@media(max-width:700px){{.report-container{{padding:24px 18px}}h1{{font-size:29px}}.visual-group-two-column{{grid-template-columns:1fr}}.section{{padding:28px 0}}.kpi-grid{{grid-template-columns:1fr 1fr}}}}
@media(max-width:440px){{.kpi-grid{{grid-template-columns:1fr}}}}
""".replace(
            "\n", ""
        )

    @staticmethod
    def validate_html(document: str) -> None:
        lowered = document.lower()
        if not (lowered.startswith("<!doctype html") and lowered.endswith("</html>")):
            raise AppError(
                "invalid_report_html", "Renderer did not produce a complete HTML document", 500
            )
        if re.search(
            r"(?:https?:|javascript:|document\s*\.\s*cookie|(?:fetch|import)\s*\(|"
            r"localstorage|sessionstorage|xmlhttprequest|websocket)|"
            r"<iframe|<object|<embed|<base|<form",
            lowered,
        ):
            raise AppError("unsafe_report_html", "Rendered report failed security validation", 500)

        style_blocks = re.findall(r"<style[^>]*>([\s\S]*?)</style>", document, re.I)
        for style in style_blocks:
            if style.count("{") != style.count("}"):
                raise AppError(
                    "malformed_report_css", "Rendered report contains unbalanced CSS", 500
                )

        class _SecurityParser(HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.errors: list[str] = []
                self.stack: list[str] = []

            def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
                if tag in {"iframe", "object", "embed", "base", "form"}:
                    self.errors.append(tag)
                for name, value in attrs:
                    normalized = (value or "").strip().lower()
                    if name.lower().startswith("on"):
                        self.errors.append("event")
                    if name.lower() in {"src", "href", "action"} and normalized.startswith(
                        ("http:", "https:", "//", "javascript:")
                    ):
                        self.errors.append("url")
                    if tag == "meta" and name.lower() == "http-equiv" and normalized == "refresh":
                        self.errors.append("refresh")
                if tag not in {"meta", "link", "img", "br", "hr", "input", "source"}:
                    self.stack.append(tag)

            def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
                self.handle_starttag(tag, attrs)
                if self.stack and self.stack[-1] == tag:
                    self.stack.pop()

            def handle_endtag(self, tag: str) -> None:
                if not self.stack or self.stack[-1] != tag:
                    self.errors.append("unbalanced_tag")
                    return
                self.stack.pop()

        parser = _SecurityParser()
        parser.feed(document)
        parser.close()
        if parser.stack:
            parser.errors.append("unclosed_tag")
        if parser.errors:
            raise AppError("unsafe_report_html", "Rendered report failed security validation", 500)


def _column_display_label(column: Any) -> str:
    label = column.label or column.field
    if label == column.field:
        return display_label_for(column.field)
    return label


def _resolved_column_semantic(column: Any, values: list[Any]) -> str:
    declared = getattr(column, "semantic_type", None) or "text"
    if declared != "text":
        return declared
    classified = classify_column_values(values)
    if classified in {"identifier", "date", "datetime"}:
        return classified
    return declared


def _x_semantic(labels: list[str]) -> str:
    parsed = [_parse_time(label) for label in labels]
    if parsed and all(item is not None for item in parsed):
        if any(item[3] for item in parsed):
            return "datetime"
        return "date"
    return "category"


def _parse_time(value: Any) -> tuple[int, int, int, bool] | None:
    text = str(value or "").strip()
    match = re.match(
        r"^(\d{4})(?:[-/](\d{1,2})(?:[-/](\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::\d{2})?)?)?)?$",
        text,
    )
    if not match:
        return None
    return (
        int(match.group(1)),
        int(match.group(2) or 1),
        int(match.group(3) or 1),
        bool(match.group(4)),
    )


def _number_or_value(value: Any) -> Any:
    if value is None or value == "":
        return None
    try:
        text = str(value).replace(",", "").rstrip("%")
        return float(text) if "." in text else int(text)
    except (TypeError, ValueError):
        return str(value)


def _sort_value(value: Any) -> tuple[int, float | str]:
    try:
        return (0, float(str(value).replace(",", "").rstrip("%")))
    except (TypeError, ValueError):
        return (1, "" if value is None else str(value))
