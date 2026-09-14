// Test-only scalar Excel evaluator. Runs the deployed formula text, not a second
// implementation of the demand rules. IF is lazy; AND/OR evaluate all arguments.
const fs = require('fs');
const assert = require('assert/strict');
const source = fs.readFileSync(process.argv[2], 'utf8');
const definitions = new Function(source.slice(0, source.indexOf('const argv = Context')) +
    ';return {listingFormulas, calculateListingForecast, FIELD_DEFINITIONS, OPTIONAL_FIELD_DEFINITIONS};')();
const fields = [...Object.keys(definitions.FIELD_DEFINITIONS), ...Object.keys(definitions.OPTIONAL_FIELD_DEFINITIONS)];
function letter(n) { let v=''; for (;n;n=Math.floor((n-1)/26)) v=String.fromCharCode(65+(n-1)%26)+v; return v; }
const columns = Object.fromEntries(fields.map((f,i)=>[f,{columnLetter:letter(i+1)}]));
const formulas=definitions.listingFormulas(columns,2);
for (const [field,formula] of Object.entries(formulas)) assert.ok(formula.length<8192, field+' exceeds Excel formula limit');
function parse(formula) {
    const tokens=formula.slice(1).match(/"(?:[^"]|"")*"|'[^']+'!\$?[A-Z]+\$?\d+|\$?[A-Z]+\$?\d+|\d+(?:\.\d+)?|[A-Z_][A-Z_0-9]*|<>|<=|>=|[()+\-*/^&,=<>]/gi);
    let pos=0;
    const priority={'=':1,'<>':1,'<':1,'>':1,'<=':1,'>=':1,'&':2,'+':3,'-':3,'*':4,'/':4,'^':5};
    function expr(min=0) {
        let token=tokens[pos++], left;
        if (token==='(') {left=expr();assert.equal(tokens[pos++],')');}
        else if (token==='-') left={op:'neg',args:[expr(6)]};
        else if (token.startsWith('"')) left={value:token.slice(1,-1).replace(/""/g,'"')};
        else if (/^\d/.test(token)) left={value:Number(token)};
        else if (token==='TRUE'||token==='FALSE') left={value:token==='TRUE'};
        else if (tokens[pos]==='(') {
            pos++; const args=[];
            if(tokens[pos]!==')') {do {args.push(expr());if(tokens[pos]!==',')break;pos++;}while(true);}
            assert.equal(tokens[pos++],')'); left={op:token.toUpperCase(),args};
        } else left={ref:token.replace(/\$/g,'')};
        while(pos<tokens.length && priority[tokens[pos]]!==undefined && priority[tokens[pos]]>=min) {
            const op=tokens[pos++];left={op,args:[left,expr(priority[op]+1)]};
        }
        return left;
    }
    const result=expr();assert.equal(pos,tokens.length,'unparsed formula suffix');return result;
}
const ast=Object.fromEntries(Object.entries(formulas).map(([f,v])=>[f,parse(v)]));
const rules={B5:20,B6:30,B7:90,B8:10,B9:.2,B10:.25,B11:-.2,B12:.1,B13:.25,B14:5,B15:.3,B20:10,
    B17:.4,C17:.3,D17:.3,B18:.6,C18:.3,D18:.1,B19:.7,C19:.2,D19:.1};
const number = x => x===''?0:Number(x);
function evaluate(node,read) {
    if ('value' in node)return node.value;
    if (node.ref)return read(node.ref);
    if (node.op==='IF')return evaluate(node.args[evaluate(node.args[0],read)?1:2],read);
    const a=node.args.map(x=>evaluate(x,read));
    const n=a.map(number);
    switch(node.op) {
        case '+':return n[0]+n[1];case '-':return n[0]-n[1];case '*':return n[0]*n[1];
        case '/':if(!n[1])throw Error('Division by zero');return n[0]/n[1];
        case 'neg':return -n[0];case '&':return String(a[0])+String(a[1]);
        case '=':return a[0]===a[1] || (a[0]===''&&a[1]===0)||(a[1]===''&&a[0]===0);
        case '<>':return !evaluate({op:'=',args:node.args},read);
        case '>':return n[0]>n[1];case '<':return n[0]<n[1];case '>=':return n[0]>=n[1];case '<=':return n[0]<=n[1];
        case 'AND':return a.every(Boolean);case 'OR':return a.some(Boolean);case 'NOT':return !a[0];
        case 'MIN':return Math.min(...n);case 'MAX':return Math.max(...n);
        case 'SUM':return a.filter(x=>typeof x==='number').reduce((v,x)=>v+x,0);
        case 'COUNT':return a.filter(x=>typeof x==='number').length;
        case 'ISNUMBER':return typeof a[0]==='number';case 'ABS':return Math.abs(n[0]);
        case 'ROUND':return Math.sign(n[0])*Math.floor(Math.abs(n[0])*10**n[1]+.5)/10**n[1];
        case 'ROUNDUP':return Math.sign(n[0])*Math.ceil(Math.abs(n[0])*10**n[1])/10**n[1];
        case 'TRIM':return String(a[0]).trim().replace(/ +/g,' ');case 'LEFT':return String(a[0]).slice(0,n[1]);
        default:throw Error('Unsupported function '+node.op);
    }
}
function calculate(values={}, manual={}) {
    const data={sales_7d:7,sales_14d:14,sales_30d:30,revenue_7d:70,revenue_14d:140,revenue_30d:300,
        ad_spend_7d:0,ad_spend_14d:0,ad_spend_30d:0,fba_available:10,reserved:0,inbound:0, ...values};
    const computed=definitions.calculateListingForecast(data, {weights:[[rules.B17,rules.C17,rules.D17],[rules.B18,rules.C18,rules.D18],[rules.B19,rules.C19,rules.D19]],smoothingSales:rules.B5,highSalesBoundary:rules.B6,trendMinimumSales:rules.B8,growthThreshold:rules.B9,declineThreshold:rules.B11,stableSpread:rules.B10,priceChangeThreshold:rules.B12,adChangeThreshold:rules.B15,adChangeMinimumAmount:rules.B20}).values;
    const cache={};const reading=new Set();
    const fieldAt=Object.fromEntries(fields.map(f=>[columns[f].columnLetter+'2',f]));
    function read(ref) {
        if(ref.startsWith("'ListingRules'!"))return rules[ref.split('!')[1]];
        const field=fieldAt[ref];assert.ok(field,'unknown reference '+ref);
        if(field in manual)return manual[field];
        if(field in computed)return computed[field];
        if(field in cache)return cache[field];
        if(ast[field]) {
            assert.ok(!reading.has(field),'circular formula '+field);reading.add(field);
            const result=evaluate(ast[field],read);reading.delete(field);
            if(typeof result==='number')assert.ok(Number.isFinite(result),field);
            return cache[field]=result;
        }
        return data[field]??'';
    }
    return Object.fromEntries([...Object.keys(ast),...Object.keys(computed)].map(f=>[f,read(columns[f].columnLetter+'2')]));
}
const scenarios=[
    [[7,14,30],30,'稳定'], [[61,108,212],237,'增长'],
    [[34,68,162],155,'稳定'], [[5,7,9],12,'增长'],
    [[5,24,46],35,'放量后回落'], [[7,31,40],43,'放量后回落'],
    [[20,39,47],77,'增长后高位维持'], [[2,2,2],2,'近期启动／恢复出单'],
    [[0,0,0],0,'无销量'], [[0,5,20],7,'近期无销量'],
    [[1,5,16],10,'下降'], [[7,12,50],32,'近期反弹'],
    [[7,14,60],36,'下降后低位维持']
];
for(const [sales,expected,status] of scenarios) {
    const v=calculate({sales_7d:sales[0],sales_14d:sales[1],sales_30d:sales[2]});
    assert.equal(v.system_monthly_sales,expected,JSON.stringify(sales));
    assert.equal(v.sales_status,status,JSON.stringify(sales));
}
const stable=calculate();assert.equal(stable.forecast_confidence,'高');assert.equal(stable.suggested_replenishment,80);
assert.equal(stable.exception_reason,'');
assert.equal(stable.monthly_sales_method,'平稳加权 40/30/30');
const overlap=calculate({sales_7d:17,sales_14d:31,sales_30d:63});
assert.equal(overlap.sales_status,'稳定');assert.equal(overlap.system_monthly_sales,65);
assert.equal(calculate({sales_7d:2,sales_14d:6,sales_30d:16}).monthly_sales_method,'近期减弱 70/20/10＋低样本平滑');
rules.B17=.5;rules.C17=.25;rules.D17=.25;
assert.equal(calculate().monthly_sales_method,'平稳加权 50/25/25');
rules.B17=.4;rules.C17=.3;rules.D17=.3;
assert.equal(calculate({link_status:'已删除',fba_available:0}).exception_reason,'');
assert.equal(calculate({sales_7d:0}).exception_reason,'近7天无销量');
assert.equal(calculate({sales_7d:6,sales_14d:14,sales_30d:24,revenue_7d:60,revenue_14d:140}).exception_reason,'前期放量后回落');
assert.equal(calculate({ad_spend_7d:1,ad_spend_14d:1,ad_spend_30d:1}).forecast_confidence,'高');
for (const sales of [[7,14,30],[2,6,16],[0,2,5],[6,14,24],[7,12,50]]) {
    const value=calculate({sales_7d:sales[0],sales_14d:sales[1],sales_30d:sales[2]});
    assert.equal(Boolean(value.exception_reason),value.forecast_confidence==='低');
}
for(const bad of [null,'bad',-1]) {
    const v=calculate({sales_7d:bad});assert.equal(v.system_monthly_sales,'');assert.equal(v.suggested_replenishment,'');
}
assert.equal(calculate({sales_7d:20,sales_14d:10}).system_monthly_sales,'');
for(const status of ['停售','已删除','已删除-原因']) {
    const v=calculate({link_status:status},{final_monthly_sales:999});assert.equal(v.system_monthly_sales,0);assert.equal(v.suggested_replenishment,'');
}
for(const status of ['停补','清库存','停售']) {
    const v=calculate({replenishment_status:status});assert.equal(v.system_monthly_sales,30);assert.equal(v.final_monthly_sales,30);assert.equal(v.stock_coverage_days,10);assert.equal(v.suggested_replenishment,'');assert.equal(v.forecast_confidence,'高');
}
assert.equal(calculate({link_status:'即将下架'}).suggested_replenishment,'');
assert.equal(calculate({link_status:'冻结',replenishment_status:'停补'},{final_monthly_sales:999}).suggested_replenishment,'');
assert.equal(calculate({link_status:'冻结'}).system_monthly_sales,'');
assert.equal(calculate({link_status:'其他'}).system_monthly_sales,'');
assert.equal(calculate({replenishment_status:'其他'}).suggested_replenishment,'');
assert.equal(calculate({sales_7d:0,sales_14d:0,sales_30d:0}).suggested_replenishment,'');
assert.equal(calculate({}, {final_monthly_sales:-10}).suggested_replenishment,'');
assert.equal(calculate({inbound:null}).suggested_replenishment,'');
assert.equal(calculate({fba_available:0}).forecast_confidence,'低');
const ads=calculate({ad_spend_7d:70,ad_spend_14d:140,ad_spend_30d:300});
assert.equal(ads.ad_status,'高广告费');assert.equal(ads.forecast_confidence,'高');
assert.equal(calculate({ad_spend_14d:null}).forecast_confidence,'中');
assert.equal(calculate({ad_spend_7d:10,ad_spend_14d:10,ad_spend_30d:10}).forecast_confidence,'低');
assert.equal(calculate({revenue_7d:140,revenue_14d:210}).forecast_confidence,'低');
for(const attr of ['常规品','夏季品','冬季品']) {
    assert.equal(calculate({product_attribute:attr}).system_monthly_sales,30);
}
assert.match(calculate({product_attribute:'夏季品',sales_7d:20,sales_14d:39,sales_30d:47}).exception_reason,/季节品走势变化/);
// Formula generation must track header relocation, including optional attribute.
const shifted=Object.fromEntries(fields.map((f,i)=>[f,{columnLetter:letter(fields.length-i)}]));
assert.notEqual(definitions.listingFormulas(shifted,25).suggested_replenishment,formulas.suggested_replenishment);
assert.equal(calculate({}, {final_monthly_sales:100}).suggested_replenishment,290);
assert.equal(calculate({link_status:'冻结'}, {final_monthly_sales:100}).suggested_replenishment,'');
assert.equal(calculate({replenishment_status:'停补'}, {final_monthly_sales:100}).suggested_replenishment,'');
console.log(JSON.stringify({cases:scenarios.length,formulas:Object.keys(formulas).length,maxFormulaLength:Math.max(...Object.values(formulas).map(x=>x.length)),status:'passed'}));
