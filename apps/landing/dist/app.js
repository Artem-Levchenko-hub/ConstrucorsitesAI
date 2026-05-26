(()=>{var fd=Object.create;var gs=Object.defineProperty;var md=Object.getOwnPropertyDescriptor;var gd=Object.getOwnPropertyNames;var vd=Object.getPrototypeOf,xd=Object.prototype.hasOwnProperty;var Ge=(e,t)=>()=>(t||e((t={exports:{}}).exports,t),t.exports);var hd=(e,t,n,a)=>{if(t&&typeof t=="object"||typeof t=="function")for(let i of gd(t))!xd.call(e,i)&&i!==n&&gs(e,i,{get:()=>t[i],enumerable:!(a=md(t,i))||a.enumerable});return e};var ga=(e,t,n)=>(n=e!=null?fd(vd(e)):{},hd(t||!e||!e.__esModule?gs(n,"default",{value:e,enumerable:!0}):n,e));var Ms=Ge(P=>{"use strict";var kn=Symbol.for("react.element"),bd=Symbol.for("react.portal"),yd=Symbol.for("react.fragment"),wd=Symbol.for("react.strict_mode"),kd=Symbol.for("react.profiler"),Nd=Symbol.for("react.provider"),zd=Symbol.for("react.context"),Sd=Symbol.for("react.forward_ref"),Cd=Symbol.for("react.suspense"),Md=Symbol.for("react.memo"),Ed=Symbol.for("react.lazy"),vs=Symbol.iterator;function Pd(e){return e===null||typeof e!="object"?null:(e=vs&&e[vs]||e["@@iterator"],typeof e=="function"?e:null)}var bs={isMounted:function(){return!1},enqueueForceUpdate:function(){},enqueueReplaceState:function(){},enqueueSetState:function(){}},ys=Object.assign,ws={};function Wt(e,t,n){this.props=e,this.context=t,this.refs=ws,this.updater=n||bs}Wt.prototype.isReactComponent={};Wt.prototype.setState=function(e,t){if(typeof e!="object"&&typeof e!="function"&&e!=null)throw Error("setState(...): takes an object of state variables to update or a function which returns an object of state variables.");this.updater.enqueueSetState(this,e,t,"setState")};Wt.prototype.forceUpdate=function(e){this.updater.enqueueForceUpdate(this,e,"forceUpdate")};function ks(){}ks.prototype=Wt.prototype;function Vr(e,t,n){this.props=e,this.context=t,this.refs=ws,this.updater=n||bs}var Wr=Vr.prototype=new ks;Wr.constructor=Vr;ys(Wr,Wt.prototype);Wr.isPureReactComponent=!0;var xs=Array.isArray,Ns=Object.prototype.hasOwnProperty,Hr={current:null},zs={key:!0,ref:!0,__self:!0,__source:!0};function Ss(e,t,n){var a,i={},o=null,s=null;if(t!=null)for(a in t.ref!==void 0&&(s=t.ref),t.key!==void 0&&(o=""+t.key),t)Ns.call(t,a)&&!zs.hasOwnProperty(a)&&(i[a]=t[a]);var l=arguments.length-2;if(l===1)i.children=n;else if(1<l){for(var c=Array(l),p=0;p<l;p++)c[p]=arguments[p+2];i.children=c}if(e&&e.defaultProps)for(a in l=e.defaultProps,l)i[a]===void 0&&(i[a]=l[a]);return{$$typeof:kn,type:e,key:o,ref:s,props:i,_owner:Hr.current}}function Td(e,t){return{$$typeof:kn,type:e.type,key:t,ref:e.ref,props:e.props,_owner:e._owner}}function Yr(e){return typeof e=="object"&&e!==null&&e.$$typeof===kn}function Rd(e){var t={"=":"=0",":":"=2"};return"$"+e.replace(/[=:]/g,function(n){return t[n]})}var hs=/\/+/g;function Ur(e,t){return typeof e=="object"&&e!==null&&e.key!=null?Rd(""+e.key):t.toString(36)}function xa(e,t,n,a,i){var o=typeof e;(o==="undefined"||o==="boolean")&&(e=null);var s=!1;if(e===null)s=!0;else switch(o){case"string":case"number":s=!0;break;case"object":switch(e.$$typeof){case kn:case bd:s=!0}}if(s)return s=e,i=i(s),e=a===""?"."+Ur(s,0):a,xs(i)?(n="",e!=null&&(n=e.replace(hs,"$&/")+"/"),xa(i,t,n,"",function(p){return p})):i!=null&&(Yr(i)&&(i=Td(i,n+(!i.key||s&&s.key===i.key?"":(""+i.key).replace(hs,"$&/")+"/")+e)),t.push(i)),1;if(s=0,a=a===""?".":a+":",xs(e))for(var l=0;l<e.length;l++){o=e[l];var c=a+Ur(o,l);s+=xa(o,t,n,c,i)}else if(c=Pd(e),typeof c=="function")for(e=c.call(e),l=0;!(o=e.next()).done;)o=o.value,c=a+Ur(o,l++),s+=xa(o,t,n,c,i);else if(o==="object")throw t=String(e),Error("Objects are not valid as a React child (found: "+(t==="[object Object]"?"object with keys {"+Object.keys(e).join(", ")+"}":t)+"). If you meant to render a collection of children, use an array instead.");return s}function va(e,t,n){if(e==null)return e;var a=[],i=0;return xa(e,a,"","",function(o){return t.call(n,o,i++)}),a}function Ld(e){if(e._status===-1){var t=e._result;t=t(),t.then(function(n){(e._status===0||e._status===-1)&&(e._status=1,e._result=n)},function(n){(e._status===0||e._status===-1)&&(e._status=2,e._result=n)}),e._status===-1&&(e._status=0,e._result=t)}if(e._status===1)return e._result.default;throw e._result}var de={current:null},ha={transition:null},_d={ReactCurrentDispatcher:de,ReactCurrentBatchConfig:ha,ReactCurrentOwner:Hr};function Cs(){throw Error("act(...) is not supported in production builds of React.")}P.Children={map:va,forEach:function(e,t,n){va(e,function(){t.apply(this,arguments)},n)},count:function(e){var t=0;return va(e,function(){t++}),t},toArray:function(e){return va(e,function(t){return t})||[]},only:function(e){if(!Yr(e))throw Error("React.Children.only expected to receive a single React element child.");return e}};P.Component=Wt;P.Fragment=yd;P.Profiler=kd;P.PureComponent=Vr;P.StrictMode=wd;P.Suspense=Cd;P.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED=_d;P.act=Cs;P.cloneElement=function(e,t,n){if(e==null)throw Error("React.cloneElement(...): The argument must be a React element, but you passed "+e+".");var a=ys({},e.props),i=e.key,o=e.ref,s=e._owner;if(t!=null){if(t.ref!==void 0&&(o=t.ref,s=Hr.current),t.key!==void 0&&(i=""+t.key),e.type&&e.type.defaultProps)var l=e.type.defaultProps;for(c in t)Ns.call(t,c)&&!zs.hasOwnProperty(c)&&(a[c]=t[c]===void 0&&l!==void 0?l[c]:t[c])}var c=arguments.length-2;if(c===1)a.children=n;else if(1<c){l=Array(c);for(var p=0;p<c;p++)l[p]=arguments[p+2];a.children=l}return{$$typeof:kn,type:e.type,key:i,ref:o,props:a,_owner:s}};P.createContext=function(e){return e={$$typeof:zd,_currentValue:e,_currentValue2:e,_threadCount:0,Provider:null,Consumer:null,_defaultValue:null,_globalName:null},e.Provider={$$typeof:Nd,_context:e},e.Consumer=e};P.createElement=Ss;P.createFactory=function(e){var t=Ss.bind(null,e);return t.type=e,t};P.createRef=function(){return{current:null}};P.forwardRef=function(e){return{$$typeof:Sd,render:e}};P.isValidElement=Yr;P.lazy=function(e){return{$$typeof:Ed,_payload:{_status:-1,_result:e},_init:Ld}};P.memo=function(e,t){return{$$typeof:Md,type:e,compare:t===void 0?null:t}};P.startTransition=function(e){var t=ha.transition;ha.transition={};try{e()}finally{ha.transition=t}};P.unstable_act=Cs;P.useCallback=function(e,t){return de.current.useCallback(e,t)};P.useContext=function(e){return de.current.useContext(e)};P.useDebugValue=function(){};P.useDeferredValue=function(e){return de.current.useDeferredValue(e)};P.useEffect=function(e,t){return de.current.useEffect(e,t)};P.useId=function(){return de.current.useId()};P.useImperativeHandle=function(e,t,n){return de.current.useImperativeHandle(e,t,n)};P.useInsertionEffect=function(e,t){return de.current.useInsertionEffect(e,t)};P.useLayoutEffect=function(e,t){return de.current.useLayoutEffect(e,t)};P.useMemo=function(e,t){return de.current.useMemo(e,t)};P.useReducer=function(e,t,n){return de.current.useReducer(e,t,n)};P.useRef=function(e){return de.current.useRef(e)};P.useState=function(e){return de.current.useState(e)};P.useSyncExternalStore=function(e,t,n){return de.current.useSyncExternalStore(e,t,n)};P.useTransition=function(){return de.current.useTransition()};P.version="18.3.1"});var ba=Ge((e0,Es)=>{"use strict";Es.exports=Ms()});var Os=Ge(j=>{"use strict";function Xr(e,t){var n=e.length;e.push(t);e:for(;0<n;){var a=n-1>>>1,i=e[a];if(0<ya(i,t))e[a]=t,e[n]=i,n=a;else break e}}function Ie(e){return e.length===0?null:e[0]}function ka(e){if(e.length===0)return null;var t=e[0],n=e.pop();if(n!==t){e[0]=n;e:for(var a=0,i=e.length,o=i>>>1;a<o;){var s=2*(a+1)-1,l=e[s],c=s+1,p=e[c];if(0>ya(l,n))c<i&&0>ya(p,l)?(e[a]=p,e[c]=n,a=c):(e[a]=l,e[s]=n,a=s);else if(c<i&&0>ya(p,n))e[a]=p,e[c]=n,a=c;else break e}}return t}function ya(e,t){var n=e.sortIndex-t.sortIndex;return n!==0?n:e.id-t.id}typeof performance=="object"&&typeof performance.now=="function"?(Ps=performance,j.unstable_now=function(){return Ps.now()}):(qr=Date,Ts=qr.now(),j.unstable_now=function(){return qr.now()-Ts});var Ps,qr,Ts,Ue=[],lt=[],Id=1,Ce=null,oe=3,Na=!1,Pt=!1,zn=!1,_s=typeof setTimeout=="function"?setTimeout:null,Is=typeof clearTimeout=="function"?clearTimeout:null,Rs=typeof setImmediate<"u"?setImmediate:null;typeof navigator<"u"&&navigator.scheduling!==void 0&&navigator.scheduling.isInputPending!==void 0&&navigator.scheduling.isInputPending.bind(navigator.scheduling);function Kr(e){for(var t=Ie(lt);t!==null;){if(t.callback===null)ka(lt);else if(t.startTime<=e)ka(lt),t.sortIndex=t.expirationTime,Xr(Ue,t);else break;t=Ie(lt)}}function Jr(e){if(zn=!1,Kr(e),!Pt)if(Ie(Ue)!==null)Pt=!0,ei(Zr);else{var t=Ie(lt);t!==null&&ti(Jr,t.startTime-e)}}function Zr(e,t){Pt=!1,zn&&(zn=!1,Is(Sn),Sn=-1),Na=!0;var n=oe;try{for(Kr(t),Ce=Ie(Ue);Ce!==null&&(!(Ce.expirationTime>t)||e&&!Ds());){var a=Ce.callback;if(typeof a=="function"){Ce.callback=null,oe=Ce.priorityLevel;var i=a(Ce.expirationTime<=t);t=j.unstable_now(),typeof i=="function"?Ce.callback=i:Ce===Ie(Ue)&&ka(Ue),Kr(t)}else ka(Ue);Ce=Ie(Ue)}if(Ce!==null)var o=!0;else{var s=Ie(lt);s!==null&&ti(Jr,s.startTime-t),o=!1}return o}finally{Ce=null,oe=n,Na=!1}}var za=!1,wa=null,Sn=-1,$s=5,js=-1;function Ds(){return!(j.unstable_now()-js<$s)}function Qr(){if(wa!==null){var e=j.unstable_now();js=e;var t=!0;try{t=wa(!0,e)}finally{t?Nn():(za=!1,wa=null)}}else za=!1}var Nn;typeof Rs=="function"?Nn=function(){Rs(Qr)}:typeof MessageChannel<"u"?(Gr=new MessageChannel,Ls=Gr.port2,Gr.port1.onmessage=Qr,Nn=function(){Ls.postMessage(null)}):Nn=function(){_s(Qr,0)};var Gr,Ls;function ei(e){wa=e,za||(za=!0,Nn())}function ti(e,t){Sn=_s(function(){e(j.unstable_now())},t)}j.unstable_IdlePriority=5;j.unstable_ImmediatePriority=1;j.unstable_LowPriority=4;j.unstable_NormalPriority=3;j.unstable_Profiling=null;j.unstable_UserBlockingPriority=2;j.unstable_cancelCallback=function(e){e.callback=null};j.unstable_continueExecution=function(){Pt||Na||(Pt=!0,ei(Zr))};j.unstable_forceFrameRate=function(e){0>e||125<e?console.error("forceFrameRate takes a positive int between 0 and 125, forcing frame rates higher than 125 fps is not supported"):$s=0<e?Math.floor(1e3/e):5};j.unstable_getCurrentPriorityLevel=function(){return oe};j.unstable_getFirstCallbackNode=function(){return Ie(Ue)};j.unstable_next=function(e){switch(oe){case 1:case 2:case 3:var t=3;break;default:t=oe}var n=oe;oe=t;try{return e()}finally{oe=n}};j.unstable_pauseExecution=function(){};j.unstable_requestPaint=function(){};j.unstable_runWithPriority=function(e,t){switch(e){case 1:case 2:case 3:case 4:case 5:break;default:e=3}var n=oe;oe=e;try{return t()}finally{oe=n}};j.unstable_scheduleCallback=function(e,t,n){var a=j.unstable_now();switch(typeof n=="object"&&n!==null?(n=n.delay,n=typeof n=="number"&&0<n?a+n:a):n=a,e){case 1:var i=-1;break;case 2:i=250;break;case 5:i=1073741823;break;case 4:i=1e4;break;default:i=5e3}return i=n+i,e={id:Id++,callback:t,priorityLevel:e,startTime:n,expirationTime:i,sortIndex:-1},n>a?(e.sortIndex=n,Xr(lt,e),Ie(Ue)===null&&e===Ie(lt)&&(zn?(Is(Sn),Sn=-1):zn=!0,ti(Jr,n-a))):(e.sortIndex=i,Xr(Ue,e),Pt||Na||(Pt=!0,ei(Zr))),e};j.unstable_shouldYield=Ds;j.unstable_wrapCallback=function(e){var t=oe;return function(){var n=oe;oe=t;try{return e.apply(this,arguments)}finally{oe=n}}}});var Fs=Ge((n0,As)=>{"use strict";As.exports=Os()});var Wp=Ge(Se=>{"use strict";var $d=ba(),Ne=Fs();function b(e){for(var t="https://reactjs.org/docs/error-decoder.html?invariant="+e,n=1;n<arguments.length;n++)t+="&args[]="+encodeURIComponent(arguments[n]);return"Minified React error #"+e+"; visit "+t+" for the full message or use the non-minified dev environment for full errors and additional helpful warnings."}var ql=new Set,qn={};function Ut(e,t){un(e,t),un(e+"Capture",t)}function un(e,t){for(qn[e]=t,e=0;e<t.length;e++)ql.add(t[e])}var tt=!(typeof window>"u"||typeof window.document>"u"||typeof window.document.createElement>"u"),zi=Object.prototype.hasOwnProperty,jd=/^[:A-Z_a-z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u02FF\u0370-\u037D\u037F-\u1FFF\u200C-\u200D\u2070-\u218F\u2C00-\u2FEF\u3001-\uD7FF\uF900-\uFDCF\uFDF0-\uFFFD][:A-Z_a-z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u02FF\u0370-\u037D\u037F-\u1FFF\u200C-\u200D\u2070-\u218F\u2C00-\u2FEF\u3001-\uD7FF\uF900-\uFDCF\uFDF0-\uFFFD\-.0-9\u00B7\u0300-\u036F\u203F-\u2040]*$/,Bs={},Us={};function Dd(e){return zi.call(Us,e)?!0:zi.call(Bs,e)?!1:jd.test(e)?Us[e]=!0:(Bs[e]=!0,!1)}function Od(e,t,n,a){if(n!==null&&n.type===0)return!1;switch(typeof t){case"function":case"symbol":return!0;case"boolean":return a?!1:n!==null?!n.acceptsBooleans:(e=e.toLowerCase().slice(0,5),e!=="data-"&&e!=="aria-");default:return!1}}function Ad(e,t,n,a){if(t===null||typeof t>"u"||Od(e,t,n,a))return!0;if(a)return!1;if(n!==null)switch(n.type){case 3:return!t;case 4:return t===!1;case 5:return isNaN(t);case 6:return isNaN(t)||1>t}return!1}function me(e,t,n,a,i,o,s){this.acceptsBooleans=t===2||t===3||t===4,this.attributeName=a,this.attributeNamespace=i,this.mustUseProperty=n,this.propertyName=e,this.type=t,this.sanitizeURL=o,this.removeEmptyString=s}var ie={};"children dangerouslySetInnerHTML defaultValue defaultChecked innerHTML suppressContentEditableWarning suppressHydrationWarning style".split(" ").forEach(function(e){ie[e]=new me(e,0,!1,e,null,!1,!1)});[["acceptCharset","accept-charset"],["className","class"],["htmlFor","for"],["httpEquiv","http-equiv"]].forEach(function(e){var t=e[0];ie[t]=new me(t,1,!1,e[1],null,!1,!1)});["contentEditable","draggable","spellCheck","value"].forEach(function(e){ie[e]=new me(e,2,!1,e.toLowerCase(),null,!1,!1)});["autoReverse","externalResourcesRequired","focusable","preserveAlpha"].forEach(function(e){ie[e]=new me(e,2,!1,e,null,!1,!1)});"allowFullScreen async autoFocus autoPlay controls default defer disabled disablePictureInPicture disableRemotePlayback formNoValidate hidden loop noModule noValidate open playsInline readOnly required reversed scoped seamless itemScope".split(" ").forEach(function(e){ie[e]=new me(e,3,!1,e.toLowerCase(),null,!1,!1)});["checked","multiple","muted","selected"].forEach(function(e){ie[e]=new me(e,3,!0,e,null,!1,!1)});["capture","download"].forEach(function(e){ie[e]=new me(e,4,!1,e,null,!1,!1)});["cols","rows","size","span"].forEach(function(e){ie[e]=new me(e,6,!1,e,null,!1,!1)});["rowSpan","start"].forEach(function(e){ie[e]=new me(e,5,!1,e.toLowerCase(),null,!1,!1)});var xo=/[\-:]([a-z])/g;function ho(e){return e[1].toUpperCase()}"accent-height alignment-baseline arabic-form baseline-shift cap-height clip-path clip-rule color-interpolation color-interpolation-filters color-profile color-rendering dominant-baseline enable-background fill-opacity fill-rule flood-color flood-opacity font-family font-size font-size-adjust font-stretch font-style font-variant font-weight glyph-name glyph-orientation-horizontal glyph-orientation-vertical horiz-adv-x horiz-origin-x image-rendering letter-spacing lighting-color marker-end marker-mid marker-start overline-position overline-thickness paint-order panose-1 pointer-events rendering-intent shape-rendering stop-color stop-opacity strikethrough-position strikethrough-thickness stroke-dasharray stroke-dashoffset stroke-linecap stroke-linejoin stroke-miterlimit stroke-opacity stroke-width text-anchor text-decoration text-rendering underline-position underline-thickness unicode-bidi unicode-range units-per-em v-alphabetic v-hanging v-ideographic v-mathematical vector-effect vert-adv-y vert-origin-x vert-origin-y word-spacing writing-mode xmlns:xlink x-height".split(" ").forEach(function(e){var t=e.replace(xo,ho);ie[t]=new me(t,1,!1,e,null,!1,!1)});"xlink:actuate xlink:arcrole xlink:role xlink:show xlink:title xlink:type".split(" ").forEach(function(e){var t=e.replace(xo,ho);ie[t]=new me(t,1,!1,e,"http://www.w3.org/1999/xlink",!1,!1)});["xml:base","xml:lang","xml:space"].forEach(function(e){var t=e.replace(xo,ho);ie[t]=new me(t,1,!1,e,"http://www.w3.org/XML/1998/namespace",!1,!1)});["tabIndex","crossOrigin"].forEach(function(e){ie[e]=new me(e,1,!1,e.toLowerCase(),null,!1,!1)});ie.xlinkHref=new me("xlinkHref",1,!1,"xlink:href","http://www.w3.org/1999/xlink",!0,!1);["src","href","action","formAction"].forEach(function(e){ie[e]=new me(e,1,!1,e.toLowerCase(),null,!0,!0)});function bo(e,t,n,a){var i=ie.hasOwnProperty(t)?ie[t]:null;(i!==null?i.type!==0:a||!(2<t.length)||t[0]!=="o"&&t[0]!=="O"||t[1]!=="n"&&t[1]!=="N")&&(Ad(t,n,i,a)&&(n=null),a||i===null?Dd(t)&&(n===null?e.removeAttribute(t):e.setAttribute(t,""+n)):i.mustUseProperty?e[i.propertyName]=n===null?i.type===3?!1:"":n:(t=i.attributeName,a=i.attributeNamespace,n===null?e.removeAttribute(t):(i=i.type,n=i===3||i===4&&n===!0?"":""+n,a?e.setAttributeNS(a,t,n):e.setAttribute(t,n))))}var it=$d.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED,Sa=Symbol.for("react.element"),qt=Symbol.for("react.portal"),Qt=Symbol.for("react.fragment"),yo=Symbol.for("react.strict_mode"),Si=Symbol.for("react.profiler"),Ql=Symbol.for("react.provider"),Gl=Symbol.for("react.context"),wo=Symbol.for("react.forward_ref"),Ci=Symbol.for("react.suspense"),Mi=Symbol.for("react.suspense_list"),ko=Symbol.for("react.memo"),pt=Symbol.for("react.lazy");Symbol.for("react.scope");Symbol.for("react.debug_trace_mode");var Xl=Symbol.for("react.offscreen");Symbol.for("react.legacy_hidden");Symbol.for("react.cache");Symbol.for("react.tracing_marker");var Vs=Symbol.iterator;function Cn(e){return e===null||typeof e!="object"?null:(e=Vs&&e[Vs]||e["@@iterator"],typeof e=="function"?e:null)}var q=Object.assign,ni;function In(e){if(ni===void 0)try{throw Error()}catch(n){var t=n.stack.trim().match(/\n( *(at )?)/);ni=t&&t[1]||""}return`
`+ni+e}var ai=!1;function ri(e,t){if(!e||ai)return"";ai=!0;var n=Error.prepareStackTrace;Error.prepareStackTrace=void 0;try{if(t)if(t=function(){throw Error()},Object.defineProperty(t.prototype,"props",{set:function(){throw Error()}}),typeof Reflect=="object"&&Reflect.construct){try{Reflect.construct(t,[])}catch(p){var a=p}Reflect.construct(e,[],t)}else{try{t.call()}catch(p){a=p}e.call(t.prototype)}else{try{throw Error()}catch(p){a=p}e()}}catch(p){if(p&&a&&typeof p.stack=="string"){for(var i=p.stack.split(`
`),o=a.stack.split(`
`),s=i.length-1,l=o.length-1;1<=s&&0<=l&&i[s]!==o[l];)l--;for(;1<=s&&0<=l;s--,l--)if(i[s]!==o[l]){if(s!==1||l!==1)do if(s--,l--,0>l||i[s]!==o[l]){var c=`
`+i[s].replace(" at new "," at ");return e.displayName&&c.includes("<anonymous>")&&(c=c.replace("<anonymous>",e.displayName)),c}while(1<=s&&0<=l);break}}}finally{ai=!1,Error.prepareStackTrace=n}return(e=e?e.displayName||e.name:"")?In(e):""}function Fd(e){switch(e.tag){case 5:return In(e.type);case 16:return In("Lazy");case 13:return In("Suspense");case 19:return In("SuspenseList");case 0:case 2:case 15:return e=ri(e.type,!1),e;case 11:return e=ri(e.type.render,!1),e;case 1:return e=ri(e.type,!0),e;default:return""}}function Ei(e){if(e==null)return null;if(typeof e=="function")return e.displayName||e.name||null;if(typeof e=="string")return e;switch(e){case Qt:return"Fragment";case qt:return"Portal";case Si:return"Profiler";case yo:return"StrictMode";case Ci:return"Suspense";case Mi:return"SuspenseList"}if(typeof e=="object")switch(e.$$typeof){case Gl:return(e.displayName||"Context")+".Consumer";case Ql:return(e._context.displayName||"Context")+".Provider";case wo:var t=e.render;return e=e.displayName,e||(e=t.displayName||t.name||"",e=e!==""?"ForwardRef("+e+")":"ForwardRef"),e;case ko:return t=e.displayName||null,t!==null?t:Ei(e.type)||"Memo";case pt:t=e._payload,e=e._init;try{return Ei(e(t))}catch{}}return null}function Bd(e){var t=e.type;switch(e.tag){case 24:return"Cache";case 9:return(t.displayName||"Context")+".Consumer";case 10:return(t._context.displayName||"Context")+".Provider";case 18:return"DehydratedFragment";case 11:return e=t.render,e=e.displayName||e.name||"",t.displayName||(e!==""?"ForwardRef("+e+")":"ForwardRef");case 7:return"Fragment";case 5:return t;case 4:return"Portal";case 3:return"Root";case 6:return"Text";case 16:return Ei(t);case 8:return t===yo?"StrictMode":"Mode";case 22:return"Offscreen";case 12:return"Profiler";case 21:return"Scope";case 13:return"Suspense";case 19:return"SuspenseList";case 25:return"TracingMarker";case 1:case 0:case 17:case 2:case 14:case 15:if(typeof t=="function")return t.displayName||t.name||null;if(typeof t=="string")return t}return null}function zt(e){switch(typeof e){case"boolean":case"number":case"string":case"undefined":return e;case"object":return e;default:return""}}function Kl(e){var t=e.type;return(e=e.nodeName)&&e.toLowerCase()==="input"&&(t==="checkbox"||t==="radio")}function Ud(e){var t=Kl(e)?"checked":"value",n=Object.getOwnPropertyDescriptor(e.constructor.prototype,t),a=""+e[t];if(!e.hasOwnProperty(t)&&typeof n<"u"&&typeof n.get=="function"&&typeof n.set=="function"){var i=n.get,o=n.set;return Object.defineProperty(e,t,{configurable:!0,get:function(){return i.call(this)},set:function(s){a=""+s,o.call(this,s)}}),Object.defineProperty(e,t,{enumerable:n.enumerable}),{getValue:function(){return a},setValue:function(s){a=""+s},stopTracking:function(){e._valueTracker=null,delete e[t]}}}}function Ca(e){e._valueTracker||(e._valueTracker=Ud(e))}function Jl(e){if(!e)return!1;var t=e._valueTracker;if(!t)return!0;var n=t.getValue(),a="";return e&&(a=Kl(e)?e.checked?"true":"false":e.value),e=a,e!==n?(t.setValue(e),!0):!1}function er(e){if(e=e||(typeof document<"u"?document:void 0),typeof e>"u")return null;try{return e.activeElement||e.body}catch{return e.body}}function Pi(e,t){var n=t.checked;return q({},t,{defaultChecked:void 0,defaultValue:void 0,value:void 0,checked:n??e._wrapperState.initialChecked})}function Ws(e,t){var n=t.defaultValue==null?"":t.defaultValue,a=t.checked!=null?t.checked:t.defaultChecked;n=zt(t.value!=null?t.value:n),e._wrapperState={initialChecked:a,initialValue:n,controlled:t.type==="checkbox"||t.type==="radio"?t.checked!=null:t.value!=null}}function Zl(e,t){t=t.checked,t!=null&&bo(e,"checked",t,!1)}function Ti(e,t){Zl(e,t);var n=zt(t.value),a=t.type;if(n!=null)a==="number"?(n===0&&e.value===""||e.value!=n)&&(e.value=""+n):e.value!==""+n&&(e.value=""+n);else if(a==="submit"||a==="reset"){e.removeAttribute("value");return}t.hasOwnProperty("value")?Ri(e,t.type,n):t.hasOwnProperty("defaultValue")&&Ri(e,t.type,zt(t.defaultValue)),t.checked==null&&t.defaultChecked!=null&&(e.defaultChecked=!!t.defaultChecked)}function Hs(e,t,n){if(t.hasOwnProperty("value")||t.hasOwnProperty("defaultValue")){var a=t.type;if(!(a!=="submit"&&a!=="reset"||t.value!==void 0&&t.value!==null))return;t=""+e._wrapperState.initialValue,n||t===e.value||(e.value=t),e.defaultValue=t}n=e.name,n!==""&&(e.name=""),e.defaultChecked=!!e._wrapperState.initialChecked,n!==""&&(e.name=n)}function Ri(e,t,n){(t!=="number"||er(e.ownerDocument)!==e)&&(n==null?e.defaultValue=""+e._wrapperState.initialValue:e.defaultValue!==""+n&&(e.defaultValue=""+n))}var $n=Array.isArray;function on(e,t,n,a){if(e=e.options,t){t={};for(var i=0;i<n.length;i++)t["$"+n[i]]=!0;for(n=0;n<e.length;n++)i=t.hasOwnProperty("$"+e[n].value),e[n].selected!==i&&(e[n].selected=i),i&&a&&(e[n].defaultSelected=!0)}else{for(n=""+zt(n),t=null,i=0;i<e.length;i++){if(e[i].value===n){e[i].selected=!0,a&&(e[i].defaultSelected=!0);return}t!==null||e[i].disabled||(t=e[i])}t!==null&&(t.selected=!0)}}function Li(e,t){if(t.dangerouslySetInnerHTML!=null)throw Error(b(91));return q({},t,{value:void 0,defaultValue:void 0,children:""+e._wrapperState.initialValue})}function Ys(e,t){var n=t.value;if(n==null){if(n=t.children,t=t.defaultValue,n!=null){if(t!=null)throw Error(b(92));if($n(n)){if(1<n.length)throw Error(b(93));n=n[0]}t=n}t==null&&(t=""),n=t}e._wrapperState={initialValue:zt(n)}}function ec(e,t){var n=zt(t.value),a=zt(t.defaultValue);n!=null&&(n=""+n,n!==e.value&&(e.value=n),t.defaultValue==null&&e.defaultValue!==n&&(e.defaultValue=n)),a!=null&&(e.defaultValue=""+a)}function qs(e){var t=e.textContent;t===e._wrapperState.initialValue&&t!==""&&t!==null&&(e.value=t)}function tc(e){switch(e){case"svg":return"http://www.w3.org/2000/svg";case"math":return"http://www.w3.org/1998/Math/MathML";default:return"http://www.w3.org/1999/xhtml"}}function _i(e,t){return e==null||e==="http://www.w3.org/1999/xhtml"?tc(t):e==="http://www.w3.org/2000/svg"&&t==="foreignObject"?"http://www.w3.org/1999/xhtml":e}var Ma,nc=function(e){return typeof MSApp<"u"&&MSApp.execUnsafeLocalFunction?function(t,n,a,i){MSApp.execUnsafeLocalFunction(function(){return e(t,n,a,i)})}:e}(function(e,t){if(e.namespaceURI!=="http://www.w3.org/2000/svg"||"innerHTML"in e)e.innerHTML=t;else{for(Ma=Ma||document.createElement("div"),Ma.innerHTML="<svg>"+t.valueOf().toString()+"</svg>",t=Ma.firstChild;e.firstChild;)e.removeChild(e.firstChild);for(;t.firstChild;)e.appendChild(t.firstChild)}});function Qn(e,t){if(t){var n=e.firstChild;if(n&&n===e.lastChild&&n.nodeType===3){n.nodeValue=t;return}}e.textContent=t}var On={animationIterationCount:!0,aspectRatio:!0,borderImageOutset:!0,borderImageSlice:!0,borderImageWidth:!0,boxFlex:!0,boxFlexGroup:!0,boxOrdinalGroup:!0,columnCount:!0,columns:!0,flex:!0,flexGrow:!0,flexPositive:!0,flexShrink:!0,flexNegative:!0,flexOrder:!0,gridArea:!0,gridRow:!0,gridRowEnd:!0,gridRowSpan:!0,gridRowStart:!0,gridColumn:!0,gridColumnEnd:!0,gridColumnSpan:!0,gridColumnStart:!0,fontWeight:!0,lineClamp:!0,lineHeight:!0,opacity:!0,order:!0,orphans:!0,tabSize:!0,widows:!0,zIndex:!0,zoom:!0,fillOpacity:!0,floodOpacity:!0,stopOpacity:!0,strokeDasharray:!0,strokeDashoffset:!0,strokeMiterlimit:!0,strokeOpacity:!0,strokeWidth:!0},Vd=["Webkit","ms","Moz","O"];Object.keys(On).forEach(function(e){Vd.forEach(function(t){t=t+e.charAt(0).toUpperCase()+e.substring(1),On[t]=On[e]})});function ac(e,t,n){return t==null||typeof t=="boolean"||t===""?"":n||typeof t!="number"||t===0||On.hasOwnProperty(e)&&On[e]?(""+t).trim():t+"px"}function rc(e,t){e=e.style;for(var n in t)if(t.hasOwnProperty(n)){var a=n.indexOf("--")===0,i=ac(n,t[n],a);n==="float"&&(n="cssFloat"),a?e.setProperty(n,i):e[n]=i}}var Wd=q({menuitem:!0},{area:!0,base:!0,br:!0,col:!0,embed:!0,hr:!0,img:!0,input:!0,keygen:!0,link:!0,meta:!0,param:!0,source:!0,track:!0,wbr:!0});function Ii(e,t){if(t){if(Wd[e]&&(t.children!=null||t.dangerouslySetInnerHTML!=null))throw Error(b(137,e));if(t.dangerouslySetInnerHTML!=null){if(t.children!=null)throw Error(b(60));if(typeof t.dangerouslySetInnerHTML!="object"||!("__html"in t.dangerouslySetInnerHTML))throw Error(b(61))}if(t.style!=null&&typeof t.style!="object")throw Error(b(62))}}function $i(e,t){if(e.indexOf("-")===-1)return typeof t.is=="string";switch(e){case"annotation-xml":case"color-profile":case"font-face":case"font-face-src":case"font-face-uri":case"font-face-format":case"font-face-name":case"missing-glyph":return!1;default:return!0}}var ji=null;function No(e){return e=e.target||e.srcElement||window,e.correspondingUseElement&&(e=e.correspondingUseElement),e.nodeType===3?e.parentNode:e}var Di=null,sn=null,ln=null;function Qs(e){if(e=ua(e)){if(typeof Di!="function")throw Error(b(280));var t=e.stateNode;t&&(t=Er(t),Di(e.stateNode,e.type,t))}}function ic(e){sn?ln?ln.push(e):ln=[e]:sn=e}function oc(){if(sn){var e=sn,t=ln;if(ln=sn=null,Qs(e),t)for(e=0;e<t.length;e++)Qs(t[e])}}function sc(e,t){return e(t)}function lc(){}var ii=!1;function cc(e,t,n){if(ii)return e(t,n);ii=!0;try{return sc(e,t,n)}finally{ii=!1,(sn!==null||ln!==null)&&(lc(),oc())}}function Gn(e,t){var n=e.stateNode;if(n===null)return null;var a=Er(n);if(a===null)return null;n=a[t];e:switch(t){case"onClick":case"onClickCapture":case"onDoubleClick":case"onDoubleClickCapture":case"onMouseDown":case"onMouseDownCapture":case"onMouseMove":case"onMouseMoveCapture":case"onMouseUp":case"onMouseUpCapture":case"onMouseEnter":(a=!a.disabled)||(e=e.type,a=!(e==="button"||e==="input"||e==="select"||e==="textarea")),e=!a;break e;default:e=!1}if(e)return null;if(n&&typeof n!="function")throw Error(b(231,t,typeof n));return n}var Oi=!1;if(tt)try{Ht={},Object.defineProperty(Ht,"passive",{get:function(){Oi=!0}}),window.addEventListener("test",Ht,Ht),window.removeEventListener("test",Ht,Ht)}catch{Oi=!1}var Ht;function Hd(e,t,n,a,i,o,s,l,c){var p=Array.prototype.slice.call(arguments,3);try{t.apply(n,p)}catch(f){this.onError(f)}}var An=!1,tr=null,nr=!1,Ai=null,Yd={onError:function(e){An=!0,tr=e}};function qd(e,t,n,a,i,o,s,l,c){An=!1,tr=null,Hd.apply(Yd,arguments)}function Qd(e,t,n,a,i,o,s,l,c){if(qd.apply(this,arguments),An){if(An){var p=tr;An=!1,tr=null}else throw Error(b(198));nr||(nr=!0,Ai=p)}}function Vt(e){var t=e,n=e;if(e.alternate)for(;t.return;)t=t.return;else{e=t;do t=e,t.flags&4098&&(n=t.return),e=t.return;while(e)}return t.tag===3?n:null}function pc(e){if(e.tag===13){var t=e.memoizedState;if(t===null&&(e=e.alternate,e!==null&&(t=e.memoizedState)),t!==null)return t.dehydrated}return null}function Gs(e){if(Vt(e)!==e)throw Error(b(188))}function Gd(e){var t=e.alternate;if(!t){if(t=Vt(e),t===null)throw Error(b(188));return t!==e?null:e}for(var n=e,a=t;;){var i=n.return;if(i===null)break;var o=i.alternate;if(o===null){if(a=i.return,a!==null){n=a;continue}break}if(i.child===o.child){for(o=i.child;o;){if(o===n)return Gs(i),e;if(o===a)return Gs(i),t;o=o.sibling}throw Error(b(188))}if(n.return!==a.return)n=i,a=o;else{for(var s=!1,l=i.child;l;){if(l===n){s=!0,n=i,a=o;break}if(l===a){s=!0,a=i,n=o;break}l=l.sibling}if(!s){for(l=o.child;l;){if(l===n){s=!0,n=o,a=i;break}if(l===a){s=!0,a=o,n=i;break}l=l.sibling}if(!s)throw Error(b(189))}}if(n.alternate!==a)throw Error(b(190))}if(n.tag!==3)throw Error(b(188));return n.stateNode.current===n?e:t}function dc(e){return e=Gd(e),e!==null?uc(e):null}function uc(e){if(e.tag===5||e.tag===6)return e;for(e=e.child;e!==null;){var t=uc(e);if(t!==null)return t;e=e.sibling}return null}var fc=Ne.unstable_scheduleCallback,Xs=Ne.unstable_cancelCallback,Xd=Ne.unstable_shouldYield,Kd=Ne.unstable_requestPaint,G=Ne.unstable_now,Jd=Ne.unstable_getCurrentPriorityLevel,zo=Ne.unstable_ImmediatePriority,mc=Ne.unstable_UserBlockingPriority,ar=Ne.unstable_NormalPriority,Zd=Ne.unstable_LowPriority,gc=Ne.unstable_IdlePriority,zr=null,Ye=null;function eu(e){if(Ye&&typeof Ye.onCommitFiberRoot=="function")try{Ye.onCommitFiberRoot(zr,e,void 0,(e.current.flags&128)===128)}catch{}}var Ae=Math.clz32?Math.clz32:au,tu=Math.log,nu=Math.LN2;function au(e){return e>>>=0,e===0?32:31-(tu(e)/nu|0)|0}var Ea=64,Pa=4194304;function jn(e){switch(e&-e){case 1:return 1;case 2:return 2;case 4:return 4;case 8:return 8;case 16:return 16;case 32:return 32;case 64:case 128:case 256:case 512:case 1024:case 2048:case 4096:case 8192:case 16384:case 32768:case 65536:case 131072:case 262144:case 524288:case 1048576:case 2097152:return e&4194240;case 4194304:case 8388608:case 16777216:case 33554432:case 67108864:return e&130023424;case 134217728:return 134217728;case 268435456:return 268435456;case 536870912:return 536870912;case 1073741824:return 1073741824;default:return e}}function rr(e,t){var n=e.pendingLanes;if(n===0)return 0;var a=0,i=e.suspendedLanes,o=e.pingedLanes,s=n&268435455;if(s!==0){var l=s&~i;l!==0?a=jn(l):(o&=s,o!==0&&(a=jn(o)))}else s=n&~i,s!==0?a=jn(s):o!==0&&(a=jn(o));if(a===0)return 0;if(t!==0&&t!==a&&!(t&i)&&(i=a&-a,o=t&-t,i>=o||i===16&&(o&4194240)!==0))return t;if(a&4&&(a|=n&16),t=e.entangledLanes,t!==0)for(e=e.entanglements,t&=a;0<t;)n=31-Ae(t),i=1<<n,a|=e[n],t&=~i;return a}function ru(e,t){switch(e){case 1:case 2:case 4:return t+250;case 8:case 16:case 32:case 64:case 128:case 256:case 512:case 1024:case 2048:case 4096:case 8192:case 16384:case 32768:case 65536:case 131072:case 262144:case 524288:case 1048576:case 2097152:return t+5e3;case 4194304:case 8388608:case 16777216:case 33554432:case 67108864:return-1;case 134217728:case 268435456:case 536870912:case 1073741824:return-1;default:return-1}}function iu(e,t){for(var n=e.suspendedLanes,a=e.pingedLanes,i=e.expirationTimes,o=e.pendingLanes;0<o;){var s=31-Ae(o),l=1<<s,c=i[s];c===-1?(!(l&n)||l&a)&&(i[s]=ru(l,t)):c<=t&&(e.expiredLanes|=l),o&=~l}}function Fi(e){return e=e.pendingLanes&-1073741825,e!==0?e:e&1073741824?1073741824:0}function vc(){var e=Ea;return Ea<<=1,!(Ea&4194240)&&(Ea=64),e}function oi(e){for(var t=[],n=0;31>n;n++)t.push(e);return t}function pa(e,t,n){e.pendingLanes|=t,t!==536870912&&(e.suspendedLanes=0,e.pingedLanes=0),e=e.eventTimes,t=31-Ae(t),e[t]=n}function ou(e,t){var n=e.pendingLanes&~t;e.pendingLanes=t,e.suspendedLanes=0,e.pingedLanes=0,e.expiredLanes&=t,e.mutableReadLanes&=t,e.entangledLanes&=t,t=e.entanglements;var a=e.eventTimes;for(e=e.expirationTimes;0<n;){var i=31-Ae(n),o=1<<i;t[i]=0,a[i]=-1,e[i]=-1,n&=~o}}function So(e,t){var n=e.entangledLanes|=t;for(e=e.entanglements;n;){var a=31-Ae(n),i=1<<a;i&t|e[a]&t&&(e[a]|=t),n&=~i}}var _=0;function xc(e){return e&=-e,1<e?4<e?e&268435455?16:536870912:4:1}var hc,Co,bc,yc,wc,Bi=!1,Ta=[],vt=null,xt=null,ht=null,Xn=new Map,Kn=new Map,ut=[],su="mousedown mouseup touchcancel touchend touchstart auxclick dblclick pointercancel pointerdown pointerup dragend dragstart drop compositionend compositionstart keydown keypress keyup input textInput copy cut paste click change contextmenu reset submit".split(" ");function Ks(e,t){switch(e){case"focusin":case"focusout":vt=null;break;case"dragenter":case"dragleave":xt=null;break;case"mouseover":case"mouseout":ht=null;break;case"pointerover":case"pointerout":Xn.delete(t.pointerId);break;case"gotpointercapture":case"lostpointercapture":Kn.delete(t.pointerId)}}function Mn(e,t,n,a,i,o){return e===null||e.nativeEvent!==o?(e={blockedOn:t,domEventName:n,eventSystemFlags:a,nativeEvent:o,targetContainers:[i]},t!==null&&(t=ua(t),t!==null&&Co(t)),e):(e.eventSystemFlags|=a,t=e.targetContainers,i!==null&&t.indexOf(i)===-1&&t.push(i),e)}function lu(e,t,n,a,i){switch(t){case"focusin":return vt=Mn(vt,e,t,n,a,i),!0;case"dragenter":return xt=Mn(xt,e,t,n,a,i),!0;case"mouseover":return ht=Mn(ht,e,t,n,a,i),!0;case"pointerover":var o=i.pointerId;return Xn.set(o,Mn(Xn.get(o)||null,e,t,n,a,i)),!0;case"gotpointercapture":return o=i.pointerId,Kn.set(o,Mn(Kn.get(o)||null,e,t,n,a,i)),!0}return!1}function kc(e){var t=Lt(e.target);if(t!==null){var n=Vt(t);if(n!==null){if(t=n.tag,t===13){if(t=pc(n),t!==null){e.blockedOn=t,wc(e.priority,function(){bc(n)});return}}else if(t===3&&n.stateNode.current.memoizedState.isDehydrated){e.blockedOn=n.tag===3?n.stateNode.containerInfo:null;return}}}e.blockedOn=null}function Wa(e){if(e.blockedOn!==null)return!1;for(var t=e.targetContainers;0<t.length;){var n=Ui(e.domEventName,e.eventSystemFlags,t[0],e.nativeEvent);if(n===null){n=e.nativeEvent;var a=new n.constructor(n.type,n);ji=a,n.target.dispatchEvent(a),ji=null}else return t=ua(n),t!==null&&Co(t),e.blockedOn=n,!1;t.shift()}return!0}function Js(e,t,n){Wa(e)&&n.delete(t)}function cu(){Bi=!1,vt!==null&&Wa(vt)&&(vt=null),xt!==null&&Wa(xt)&&(xt=null),ht!==null&&Wa(ht)&&(ht=null),Xn.forEach(Js),Kn.forEach(Js)}function En(e,t){e.blockedOn===t&&(e.blockedOn=null,Bi||(Bi=!0,Ne.unstable_scheduleCallback(Ne.unstable_NormalPriority,cu)))}function Jn(e){function t(i){return En(i,e)}if(0<Ta.length){En(Ta[0],e);for(var n=1;n<Ta.length;n++){var a=Ta[n];a.blockedOn===e&&(a.blockedOn=null)}}for(vt!==null&&En(vt,e),xt!==null&&En(xt,e),ht!==null&&En(ht,e),Xn.forEach(t),Kn.forEach(t),n=0;n<ut.length;n++)a=ut[n],a.blockedOn===e&&(a.blockedOn=null);for(;0<ut.length&&(n=ut[0],n.blockedOn===null);)kc(n),n.blockedOn===null&&ut.shift()}var cn=it.ReactCurrentBatchConfig,ir=!0;function pu(e,t,n,a){var i=_,o=cn.transition;cn.transition=null;try{_=1,Mo(e,t,n,a)}finally{_=i,cn.transition=o}}function du(e,t,n,a){var i=_,o=cn.transition;cn.transition=null;try{_=4,Mo(e,t,n,a)}finally{_=i,cn.transition=o}}function Mo(e,t,n,a){if(ir){var i=Ui(e,t,n,a);if(i===null)fi(e,t,a,or,n),Ks(e,a);else if(lu(i,e,t,n,a))a.stopPropagation();else if(Ks(e,a),t&4&&-1<su.indexOf(e)){for(;i!==null;){var o=ua(i);if(o!==null&&hc(o),o=Ui(e,t,n,a),o===null&&fi(e,t,a,or,n),o===i)break;i=o}i!==null&&a.stopPropagation()}else fi(e,t,a,null,n)}}var or=null;function Ui(e,t,n,a){if(or=null,e=No(a),e=Lt(e),e!==null)if(t=Vt(e),t===null)e=null;else if(n=t.tag,n===13){if(e=pc(t),e!==null)return e;e=null}else if(n===3){if(t.stateNode.current.memoizedState.isDehydrated)return t.tag===3?t.stateNode.containerInfo:null;e=null}else t!==e&&(e=null);return or=e,null}function Nc(e){switch(e){case"cancel":case"click":case"close":case"contextmenu":case"copy":case"cut":case"auxclick":case"dblclick":case"dragend":case"dragstart":case"drop":case"focusin":case"focusout":case"input":case"invalid":case"keydown":case"keypress":case"keyup":case"mousedown":case"mouseup":case"paste":case"pause":case"play":case"pointercancel":case"pointerdown":case"pointerup":case"ratechange":case"reset":case"resize":case"seeked":case"submit":case"touchcancel":case"touchend":case"touchstart":case"volumechange":case"change":case"selectionchange":case"textInput":case"compositionstart":case"compositionend":case"compositionupdate":case"beforeblur":case"afterblur":case"beforeinput":case"blur":case"fullscreenchange":case"focus":case"hashchange":case"popstate":case"select":case"selectstart":return 1;case"drag":case"dragenter":case"dragexit":case"dragleave":case"dragover":case"mousemove":case"mouseout":case"mouseover":case"pointermove":case"pointerout":case"pointerover":case"scroll":case"toggle":case"touchmove":case"wheel":case"mouseenter":case"mouseleave":case"pointerenter":case"pointerleave":return 4;case"message":switch(Jd()){case zo:return 1;case mc:return 4;case ar:case Zd:return 16;case gc:return 536870912;default:return 16}default:return 16}}var mt=null,Eo=null,Ha=null;function zc(){if(Ha)return Ha;var e,t=Eo,n=t.length,a,i="value"in mt?mt.value:mt.textContent,o=i.length;for(e=0;e<n&&t[e]===i[e];e++);var s=n-e;for(a=1;a<=s&&t[n-a]===i[o-a];a++);return Ha=i.slice(e,1<a?1-a:void 0)}function Ya(e){var t=e.keyCode;return"charCode"in e?(e=e.charCode,e===0&&t===13&&(e=13)):e=t,e===10&&(e=13),32<=e||e===13?e:0}function Ra(){return!0}function Zs(){return!1}function ze(e){function t(n,a,i,o,s){this._reactName=n,this._targetInst=i,this.type=a,this.nativeEvent=o,this.target=s,this.currentTarget=null;for(var l in e)e.hasOwnProperty(l)&&(n=e[l],this[l]=n?n(o):o[l]);return this.isDefaultPrevented=(o.defaultPrevented!=null?o.defaultPrevented:o.returnValue===!1)?Ra:Zs,this.isPropagationStopped=Zs,this}return q(t.prototype,{preventDefault:function(){this.defaultPrevented=!0;var n=this.nativeEvent;n&&(n.preventDefault?n.preventDefault():typeof n.returnValue!="unknown"&&(n.returnValue=!1),this.isDefaultPrevented=Ra)},stopPropagation:function(){var n=this.nativeEvent;n&&(n.stopPropagation?n.stopPropagation():typeof n.cancelBubble!="unknown"&&(n.cancelBubble=!0),this.isPropagationStopped=Ra)},persist:function(){},isPersistent:Ra}),t}var bn={eventPhase:0,bubbles:0,cancelable:0,timeStamp:function(e){return e.timeStamp||Date.now()},defaultPrevented:0,isTrusted:0},Po=ze(bn),da=q({},bn,{view:0,detail:0}),uu=ze(da),si,li,Pn,Sr=q({},da,{screenX:0,screenY:0,clientX:0,clientY:0,pageX:0,pageY:0,ctrlKey:0,shiftKey:0,altKey:0,metaKey:0,getModifierState:To,button:0,buttons:0,relatedTarget:function(e){return e.relatedTarget===void 0?e.fromElement===e.srcElement?e.toElement:e.fromElement:e.relatedTarget},movementX:function(e){return"movementX"in e?e.movementX:(e!==Pn&&(Pn&&e.type==="mousemove"?(si=e.screenX-Pn.screenX,li=e.screenY-Pn.screenY):li=si=0,Pn=e),si)},movementY:function(e){return"movementY"in e?e.movementY:li}}),el=ze(Sr),fu=q({},Sr,{dataTransfer:0}),mu=ze(fu),gu=q({},da,{relatedTarget:0}),ci=ze(gu),vu=q({},bn,{animationName:0,elapsedTime:0,pseudoElement:0}),xu=ze(vu),hu=q({},bn,{clipboardData:function(e){return"clipboardData"in e?e.clipboardData:window.clipboardData}}),bu=ze(hu),yu=q({},bn,{data:0}),tl=ze(yu),wu={Esc:"Escape",Spacebar:" ",Left:"ArrowLeft",Up:"ArrowUp",Right:"ArrowRight",Down:"ArrowDown",Del:"Delete",Win:"OS",Menu:"ContextMenu",Apps:"ContextMenu",Scroll:"ScrollLock",MozPrintableKey:"Unidentified"},ku={8:"Backspace",9:"Tab",12:"Clear",13:"Enter",16:"Shift",17:"Control",18:"Alt",19:"Pause",20:"CapsLock",27:"Escape",32:" ",33:"PageUp",34:"PageDown",35:"End",36:"Home",37:"ArrowLeft",38:"ArrowUp",39:"ArrowRight",40:"ArrowDown",45:"Insert",46:"Delete",112:"F1",113:"F2",114:"F3",115:"F4",116:"F5",117:"F6",118:"F7",119:"F8",120:"F9",121:"F10",122:"F11",123:"F12",144:"NumLock",145:"ScrollLock",224:"Meta"},Nu={Alt:"altKey",Control:"ctrlKey",Meta:"metaKey",Shift:"shiftKey"};function zu(e){var t=this.nativeEvent;return t.getModifierState?t.getModifierState(e):(e=Nu[e])?!!t[e]:!1}function To(){return zu}var Su=q({},da,{key:function(e){if(e.key){var t=wu[e.key]||e.key;if(t!=="Unidentified")return t}return e.type==="keypress"?(e=Ya(e),e===13?"Enter":String.fromCharCode(e)):e.type==="keydown"||e.type==="keyup"?ku[e.keyCode]||"Unidentified":""},code:0,location:0,ctrlKey:0,shiftKey:0,altKey:0,metaKey:0,repeat:0,locale:0,getModifierState:To,charCode:function(e){return e.type==="keypress"?Ya(e):0},keyCode:function(e){return e.type==="keydown"||e.type==="keyup"?e.keyCode:0},which:function(e){return e.type==="keypress"?Ya(e):e.type==="keydown"||e.type==="keyup"?e.keyCode:0}}),Cu=ze(Su),Mu=q({},Sr,{pointerId:0,width:0,height:0,pressure:0,tangentialPressure:0,tiltX:0,tiltY:0,twist:0,pointerType:0,isPrimary:0}),nl=ze(Mu),Eu=q({},da,{touches:0,targetTouches:0,changedTouches:0,altKey:0,metaKey:0,ctrlKey:0,shiftKey:0,getModifierState:To}),Pu=ze(Eu),Tu=q({},bn,{propertyName:0,elapsedTime:0,pseudoElement:0}),Ru=ze(Tu),Lu=q({},Sr,{deltaX:function(e){return"deltaX"in e?e.deltaX:"wheelDeltaX"in e?-e.wheelDeltaX:0},deltaY:function(e){return"deltaY"in e?e.deltaY:"wheelDeltaY"in e?-e.wheelDeltaY:"wheelDelta"in e?-e.wheelDelta:0},deltaZ:0,deltaMode:0}),_u=ze(Lu),Iu=[9,13,27,32],Ro=tt&&"CompositionEvent"in window,Fn=null;tt&&"documentMode"in document&&(Fn=document.documentMode);var $u=tt&&"TextEvent"in window&&!Fn,Sc=tt&&(!Ro||Fn&&8<Fn&&11>=Fn),al=" ",rl=!1;function Cc(e,t){switch(e){case"keyup":return Iu.indexOf(t.keyCode)!==-1;case"keydown":return t.keyCode!==229;case"keypress":case"mousedown":case"focusout":return!0;default:return!1}}function Mc(e){return e=e.detail,typeof e=="object"&&"data"in e?e.data:null}var Gt=!1;function ju(e,t){switch(e){case"compositionend":return Mc(t);case"keypress":return t.which!==32?null:(rl=!0,al);case"textInput":return e=t.data,e===al&&rl?null:e;default:return null}}function Du(e,t){if(Gt)return e==="compositionend"||!Ro&&Cc(e,t)?(e=zc(),Ha=Eo=mt=null,Gt=!1,e):null;switch(e){case"paste":return null;case"keypress":if(!(t.ctrlKey||t.altKey||t.metaKey)||t.ctrlKey&&t.altKey){if(t.char&&1<t.char.length)return t.char;if(t.which)return String.fromCharCode(t.which)}return null;case"compositionend":return Sc&&t.locale!=="ko"?null:t.data;default:return null}}var Ou={color:!0,date:!0,datetime:!0,"datetime-local":!0,email:!0,month:!0,number:!0,password:!0,range:!0,search:!0,tel:!0,text:!0,time:!0,url:!0,week:!0};function il(e){var t=e&&e.nodeName&&e.nodeName.toLowerCase();return t==="input"?!!Ou[e.type]:t==="textarea"}function Ec(e,t,n,a){ic(a),t=sr(t,"onChange"),0<t.length&&(n=new Po("onChange","change",null,n,a),e.push({event:n,listeners:t}))}var Bn=null,Zn=null;function Au(e){Ac(e,0)}function Cr(e){var t=Jt(e);if(Jl(t))return e}function Fu(e,t){if(e==="change")return t}var Pc=!1;tt&&(tt?(_a="oninput"in document,_a||(pi=document.createElement("div"),pi.setAttribute("oninput","return;"),_a=typeof pi.oninput=="function"),La=_a):La=!1,Pc=La&&(!document.documentMode||9<document.documentMode));var La,_a,pi;function ol(){Bn&&(Bn.detachEvent("onpropertychange",Tc),Zn=Bn=null)}function Tc(e){if(e.propertyName==="value"&&Cr(Zn)){var t=[];Ec(t,Zn,e,No(e)),cc(Au,t)}}function Bu(e,t,n){e==="focusin"?(ol(),Bn=t,Zn=n,Bn.attachEvent("onpropertychange",Tc)):e==="focusout"&&ol()}function Uu(e){if(e==="selectionchange"||e==="keyup"||e==="keydown")return Cr(Zn)}function Vu(e,t){if(e==="click")return Cr(t)}function Wu(e,t){if(e==="input"||e==="change")return Cr(t)}function Hu(e,t){return e===t&&(e!==0||1/e===1/t)||e!==e&&t!==t}var Be=typeof Object.is=="function"?Object.is:Hu;function ea(e,t){if(Be(e,t))return!0;if(typeof e!="object"||e===null||typeof t!="object"||t===null)return!1;var n=Object.keys(e),a=Object.keys(t);if(n.length!==a.length)return!1;for(a=0;a<n.length;a++){var i=n[a];if(!zi.call(t,i)||!Be(e[i],t[i]))return!1}return!0}function sl(e){for(;e&&e.firstChild;)e=e.firstChild;return e}function ll(e,t){var n=sl(e);e=0;for(var a;n;){if(n.nodeType===3){if(a=e+n.textContent.length,e<=t&&a>=t)return{node:n,offset:t-e};e=a}e:{for(;n;){if(n.nextSibling){n=n.nextSibling;break e}n=n.parentNode}n=void 0}n=sl(n)}}function Rc(e,t){return e&&t?e===t?!0:e&&e.nodeType===3?!1:t&&t.nodeType===3?Rc(e,t.parentNode):"contains"in e?e.contains(t):e.compareDocumentPosition?!!(e.compareDocumentPosition(t)&16):!1:!1}function Lc(){for(var e=window,t=er();t instanceof e.HTMLIFrameElement;){try{var n=typeof t.contentWindow.location.href=="string"}catch{n=!1}if(n)e=t.contentWindow;else break;t=er(e.document)}return t}function Lo(e){var t=e&&e.nodeName&&e.nodeName.toLowerCase();return t&&(t==="input"&&(e.type==="text"||e.type==="search"||e.type==="tel"||e.type==="url"||e.type==="password")||t==="textarea"||e.contentEditable==="true")}function Yu(e){var t=Lc(),n=e.focusedElem,a=e.selectionRange;if(t!==n&&n&&n.ownerDocument&&Rc(n.ownerDocument.documentElement,n)){if(a!==null&&Lo(n)){if(t=a.start,e=a.end,e===void 0&&(e=t),"selectionStart"in n)n.selectionStart=t,n.selectionEnd=Math.min(e,n.value.length);else if(e=(t=n.ownerDocument||document)&&t.defaultView||window,e.getSelection){e=e.getSelection();var i=n.textContent.length,o=Math.min(a.start,i);a=a.end===void 0?o:Math.min(a.end,i),!e.extend&&o>a&&(i=a,a=o,o=i),i=ll(n,o);var s=ll(n,a);i&&s&&(e.rangeCount!==1||e.anchorNode!==i.node||e.anchorOffset!==i.offset||e.focusNode!==s.node||e.focusOffset!==s.offset)&&(t=t.createRange(),t.setStart(i.node,i.offset),e.removeAllRanges(),o>a?(e.addRange(t),e.extend(s.node,s.offset)):(t.setEnd(s.node,s.offset),e.addRange(t)))}}for(t=[],e=n;e=e.parentNode;)e.nodeType===1&&t.push({element:e,left:e.scrollLeft,top:e.scrollTop});for(typeof n.focus=="function"&&n.focus(),n=0;n<t.length;n++)e=t[n],e.element.scrollLeft=e.left,e.element.scrollTop=e.top}}var qu=tt&&"documentMode"in document&&11>=document.documentMode,Xt=null,Vi=null,Un=null,Wi=!1;function cl(e,t,n){var a=n.window===n?n.document:n.nodeType===9?n:n.ownerDocument;Wi||Xt==null||Xt!==er(a)||(a=Xt,"selectionStart"in a&&Lo(a)?a={start:a.selectionStart,end:a.selectionEnd}:(a=(a.ownerDocument&&a.ownerDocument.defaultView||window).getSelection(),a={anchorNode:a.anchorNode,anchorOffset:a.anchorOffset,focusNode:a.focusNode,focusOffset:a.focusOffset}),Un&&ea(Un,a)||(Un=a,a=sr(Vi,"onSelect"),0<a.length&&(t=new Po("onSelect","select",null,t,n),e.push({event:t,listeners:a}),t.target=Xt)))}function Ia(e,t){var n={};return n[e.toLowerCase()]=t.toLowerCase(),n["Webkit"+e]="webkit"+t,n["Moz"+e]="moz"+t,n}var Kt={animationend:Ia("Animation","AnimationEnd"),animationiteration:Ia("Animation","AnimationIteration"),animationstart:Ia("Animation","AnimationStart"),transitionend:Ia("Transition","TransitionEnd")},di={},_c={};tt&&(_c=document.createElement("div").style,"AnimationEvent"in window||(delete Kt.animationend.animation,delete Kt.animationiteration.animation,delete Kt.animationstart.animation),"TransitionEvent"in window||delete Kt.transitionend.transition);function Mr(e){if(di[e])return di[e];if(!Kt[e])return e;var t=Kt[e],n;for(n in t)if(t.hasOwnProperty(n)&&n in _c)return di[e]=t[n];return e}var Ic=Mr("animationend"),$c=Mr("animationiteration"),jc=Mr("animationstart"),Dc=Mr("transitionend"),Oc=new Map,pl="abort auxClick cancel canPlay canPlayThrough click close contextMenu copy cut drag dragEnd dragEnter dragExit dragLeave dragOver dragStart drop durationChange emptied encrypted ended error gotPointerCapture input invalid keyDown keyPress keyUp load loadedData loadedMetadata loadStart lostPointerCapture mouseDown mouseMove mouseOut mouseOver mouseUp paste pause play playing pointerCancel pointerDown pointerMove pointerOut pointerOver pointerUp progress rateChange reset resize seeked seeking stalled submit suspend timeUpdate touchCancel touchEnd touchStart volumeChange scroll toggle touchMove waiting wheel".split(" ");function Ct(e,t){Oc.set(e,t),Ut(t,[e])}for($a=0;$a<pl.length;$a++)ja=pl[$a],dl=ja.toLowerCase(),ul=ja[0].toUpperCase()+ja.slice(1),Ct(dl,"on"+ul);var ja,dl,ul,$a;Ct(Ic,"onAnimationEnd");Ct($c,"onAnimationIteration");Ct(jc,"onAnimationStart");Ct("dblclick","onDoubleClick");Ct("focusin","onFocus");Ct("focusout","onBlur");Ct(Dc,"onTransitionEnd");un("onMouseEnter",["mouseout","mouseover"]);un("onMouseLeave",["mouseout","mouseover"]);un("onPointerEnter",["pointerout","pointerover"]);un("onPointerLeave",["pointerout","pointerover"]);Ut("onChange","change click focusin focusout input keydown keyup selectionchange".split(" "));Ut("onSelect","focusout contextmenu dragend focusin keydown keyup mousedown mouseup selectionchange".split(" "));Ut("onBeforeInput",["compositionend","keypress","textInput","paste"]);Ut("onCompositionEnd","compositionend focusout keydown keypress keyup mousedown".split(" "));Ut("onCompositionStart","compositionstart focusout keydown keypress keyup mousedown".split(" "));Ut("onCompositionUpdate","compositionupdate focusout keydown keypress keyup mousedown".split(" "));var Dn="abort canplay canplaythrough durationchange emptied encrypted ended error loadeddata loadedmetadata loadstart pause play playing progress ratechange resize seeked seeking stalled suspend timeupdate volumechange waiting".split(" "),Qu=new Set("cancel close invalid load scroll toggle".split(" ").concat(Dn));function fl(e,t,n){var a=e.type||"unknown-event";e.currentTarget=n,Qd(a,t,void 0,e),e.currentTarget=null}function Ac(e,t){t=(t&4)!==0;for(var n=0;n<e.length;n++){var a=e[n],i=a.event;a=a.listeners;e:{var o=void 0;if(t)for(var s=a.length-1;0<=s;s--){var l=a[s],c=l.instance,p=l.currentTarget;if(l=l.listener,c!==o&&i.isPropagationStopped())break e;fl(i,l,p),o=c}else for(s=0;s<a.length;s++){if(l=a[s],c=l.instance,p=l.currentTarget,l=l.listener,c!==o&&i.isPropagationStopped())break e;fl(i,l,p),o=c}}}if(nr)throw e=Ai,nr=!1,Ai=null,e}function A(e,t){var n=t[Gi];n===void 0&&(n=t[Gi]=new Set);var a=e+"__bubble";n.has(a)||(Fc(t,e,2,!1),n.add(a))}function ui(e,t,n){var a=0;t&&(a|=4),Fc(n,e,a,t)}var Da="_reactListening"+Math.random().toString(36).slice(2);function ta(e){if(!e[Da]){e[Da]=!0,ql.forEach(function(n){n!=="selectionchange"&&(Qu.has(n)||ui(n,!1,e),ui(n,!0,e))});var t=e.nodeType===9?e:e.ownerDocument;t===null||t[Da]||(t[Da]=!0,ui("selectionchange",!1,t))}}function Fc(e,t,n,a){switch(Nc(t)){case 1:var i=pu;break;case 4:i=du;break;default:i=Mo}n=i.bind(null,t,n,e),i=void 0,!Oi||t!=="touchstart"&&t!=="touchmove"&&t!=="wheel"||(i=!0),a?i!==void 0?e.addEventListener(t,n,{capture:!0,passive:i}):e.addEventListener(t,n,!0):i!==void 0?e.addEventListener(t,n,{passive:i}):e.addEventListener(t,n,!1)}function fi(e,t,n,a,i){var o=a;if(!(t&1)&&!(t&2)&&a!==null)e:for(;;){if(a===null)return;var s=a.tag;if(s===3||s===4){var l=a.stateNode.containerInfo;if(l===i||l.nodeType===8&&l.parentNode===i)break;if(s===4)for(s=a.return;s!==null;){var c=s.tag;if((c===3||c===4)&&(c=s.stateNode.containerInfo,c===i||c.nodeType===8&&c.parentNode===i))return;s=s.return}for(;l!==null;){if(s=Lt(l),s===null)return;if(c=s.tag,c===5||c===6){a=o=s;continue e}l=l.parentNode}}a=a.return}cc(function(){var p=o,f=No(n),g=[];e:{var v=Oc.get(e);if(v!==void 0){var y=Po,w=e;switch(e){case"keypress":if(Ya(n)===0)break e;case"keydown":case"keyup":y=Cu;break;case"focusin":w="focus",y=ci;break;case"focusout":w="blur",y=ci;break;case"beforeblur":case"afterblur":y=ci;break;case"click":if(n.button===2)break e;case"auxclick":case"dblclick":case"mousedown":case"mousemove":case"mouseup":case"mouseout":case"mouseover":case"contextmenu":y=el;break;case"drag":case"dragend":case"dragenter":case"dragexit":case"dragleave":case"dragover":case"dragstart":case"drop":y=mu;break;case"touchcancel":case"touchend":case"touchmove":case"touchstart":y=Pu;break;case Ic:case $c:case jc:y=xu;break;case Dc:y=Ru;break;case"scroll":y=uu;break;case"wheel":y=_u;break;case"copy":case"cut":case"paste":y=bu;break;case"gotpointercapture":case"lostpointercapture":case"pointercancel":case"pointerdown":case"pointermove":case"pointerout":case"pointerover":case"pointerup":y=nl}var k=(t&4)!==0,S=!k&&e==="scroll",u=k?v!==null?v+"Capture":null:v;k=[];for(var d=p,m;d!==null;){m=d;var h=m.stateNode;if(m.tag===5&&h!==null&&(m=h,u!==null&&(h=Gn(d,u),h!=null&&k.push(na(d,h,m)))),S)break;d=d.return}0<k.length&&(v=new y(v,w,null,n,f),g.push({event:v,listeners:k}))}}if(!(t&7)){e:{if(v=e==="mouseover"||e==="pointerover",y=e==="mouseout"||e==="pointerout",v&&n!==ji&&(w=n.relatedTarget||n.fromElement)&&(Lt(w)||w[nt]))break e;if((y||v)&&(v=f.window===f?f:(v=f.ownerDocument)?v.defaultView||v.parentWindow:window,y?(w=n.relatedTarget||n.toElement,y=p,w=w?Lt(w):null,w!==null&&(S=Vt(w),w!==S||w.tag!==5&&w.tag!==6)&&(w=null)):(y=null,w=p),y!==w)){if(k=el,h="onMouseLeave",u="onMouseEnter",d="mouse",(e==="pointerout"||e==="pointerover")&&(k=nl,h="onPointerLeave",u="onPointerEnter",d="pointer"),S=y==null?v:Jt(y),m=w==null?v:Jt(w),v=new k(h,d+"leave",y,n,f),v.target=S,v.relatedTarget=m,h=null,Lt(f)===p&&(k=new k(u,d+"enter",w,n,f),k.target=m,k.relatedTarget=S,h=k),S=h,y&&w)t:{for(k=y,u=w,d=0,m=k;m;m=Yt(m))d++;for(m=0,h=u;h;h=Yt(h))m++;for(;0<d-m;)k=Yt(k),d--;for(;0<m-d;)u=Yt(u),m--;for(;d--;){if(k===u||u!==null&&k===u.alternate)break t;k=Yt(k),u=Yt(u)}k=null}else k=null;y!==null&&ml(g,v,y,k,!1),w!==null&&S!==null&&ml(g,S,w,k,!0)}}e:{if(v=p?Jt(p):window,y=v.nodeName&&v.nodeName.toLowerCase(),y==="select"||y==="input"&&v.type==="file")var N=Fu;else if(il(v))if(Pc)N=Wu;else{N=Uu;var C=Bu}else(y=v.nodeName)&&y.toLowerCase()==="input"&&(v.type==="checkbox"||v.type==="radio")&&(N=Vu);if(N&&(N=N(e,p))){Ec(g,N,n,f);break e}C&&C(e,v,p),e==="focusout"&&(C=v._wrapperState)&&C.controlled&&v.type==="number"&&Ri(v,"number",v.value)}switch(C=p?Jt(p):window,e){case"focusin":(il(C)||C.contentEditable==="true")&&(Xt=C,Vi=p,Un=null);break;case"focusout":Un=Vi=Xt=null;break;case"mousedown":Wi=!0;break;case"contextmenu":case"mouseup":case"dragend":Wi=!1,cl(g,n,f);break;case"selectionchange":if(qu)break;case"keydown":case"keyup":cl(g,n,f)}var M;if(Ro)e:{switch(e){case"compositionstart":var E="onCompositionStart";break e;case"compositionend":E="onCompositionEnd";break e;case"compositionupdate":E="onCompositionUpdate";break e}E=void 0}else Gt?Cc(e,n)&&(E="onCompositionEnd"):e==="keydown"&&n.keyCode===229&&(E="onCompositionStart");E&&(Sc&&n.locale!=="ko"&&(Gt||E!=="onCompositionStart"?E==="onCompositionEnd"&&Gt&&(M=zc()):(mt=f,Eo="value"in mt?mt.value:mt.textContent,Gt=!0)),C=sr(p,E),0<C.length&&(E=new tl(E,e,null,n,f),g.push({event:E,listeners:C}),M?E.data=M:(M=Mc(n),M!==null&&(E.data=M)))),(M=$u?ju(e,n):Du(e,n))&&(p=sr(p,"onBeforeInput"),0<p.length&&(f=new tl("onBeforeInput","beforeinput",null,n,f),g.push({event:f,listeners:p}),f.data=M))}Ac(g,t)})}function na(e,t,n){return{instance:e,listener:t,currentTarget:n}}function sr(e,t){for(var n=t+"Capture",a=[];e!==null;){var i=e,o=i.stateNode;i.tag===5&&o!==null&&(i=o,o=Gn(e,n),o!=null&&a.unshift(na(e,o,i)),o=Gn(e,t),o!=null&&a.push(na(e,o,i))),e=e.return}return a}function Yt(e){if(e===null)return null;do e=e.return;while(e&&e.tag!==5);return e||null}function ml(e,t,n,a,i){for(var o=t._reactName,s=[];n!==null&&n!==a;){var l=n,c=l.alternate,p=l.stateNode;if(c!==null&&c===a)break;l.tag===5&&p!==null&&(l=p,i?(c=Gn(n,o),c!=null&&s.unshift(na(n,c,l))):i||(c=Gn(n,o),c!=null&&s.push(na(n,c,l)))),n=n.return}s.length!==0&&e.push({event:t,listeners:s})}var Gu=/\r\n?/g,Xu=/\u0000|\uFFFD/g;function gl(e){return(typeof e=="string"?e:""+e).replace(Gu,`
`).replace(Xu,"")}function Oa(e,t,n){if(t=gl(t),gl(e)!==t&&n)throw Error(b(425))}function lr(){}var Hi=null,Yi=null;function qi(e,t){return e==="textarea"||e==="noscript"||typeof t.children=="string"||typeof t.children=="number"||typeof t.dangerouslySetInnerHTML=="object"&&t.dangerouslySetInnerHTML!==null&&t.dangerouslySetInnerHTML.__html!=null}var Qi=typeof setTimeout=="function"?setTimeout:void 0,Ku=typeof clearTimeout=="function"?clearTimeout:void 0,vl=typeof Promise=="function"?Promise:void 0,Ju=typeof queueMicrotask=="function"?queueMicrotask:typeof vl<"u"?function(e){return vl.resolve(null).then(e).catch(Zu)}:Qi;function Zu(e){setTimeout(function(){throw e})}function mi(e,t){var n=t,a=0;do{var i=n.nextSibling;if(e.removeChild(n),i&&i.nodeType===8)if(n=i.data,n==="/$"){if(a===0){e.removeChild(i),Jn(t);return}a--}else n!=="$"&&n!=="$?"&&n!=="$!"||a++;n=i}while(n);Jn(t)}function bt(e){for(;e!=null;e=e.nextSibling){var t=e.nodeType;if(t===1||t===3)break;if(t===8){if(t=e.data,t==="$"||t==="$!"||t==="$?")break;if(t==="/$")return null}}return e}function xl(e){e=e.previousSibling;for(var t=0;e;){if(e.nodeType===8){var n=e.data;if(n==="$"||n==="$!"||n==="$?"){if(t===0)return e;t--}else n==="/$"&&t++}e=e.previousSibling}return null}var yn=Math.random().toString(36).slice(2),He="__reactFiber$"+yn,aa="__reactProps$"+yn,nt="__reactContainer$"+yn,Gi="__reactEvents$"+yn,ef="__reactListeners$"+yn,tf="__reactHandles$"+yn;function Lt(e){var t=e[He];if(t)return t;for(var n=e.parentNode;n;){if(t=n[nt]||n[He]){if(n=t.alternate,t.child!==null||n!==null&&n.child!==null)for(e=xl(e);e!==null;){if(n=e[He])return n;e=xl(e)}return t}e=n,n=e.parentNode}return null}function ua(e){return e=e[He]||e[nt],!e||e.tag!==5&&e.tag!==6&&e.tag!==13&&e.tag!==3?null:e}function Jt(e){if(e.tag===5||e.tag===6)return e.stateNode;throw Error(b(33))}function Er(e){return e[aa]||null}var Xi=[],Zt=-1;function Mt(e){return{current:e}}function F(e){0>Zt||(e.current=Xi[Zt],Xi[Zt]=null,Zt--)}function D(e,t){Zt++,Xi[Zt]=e.current,e.current=t}var St={},pe=Mt(St),xe=Mt(!1),Dt=St;function fn(e,t){var n=e.type.contextTypes;if(!n)return St;var a=e.stateNode;if(a&&a.__reactInternalMemoizedUnmaskedChildContext===t)return a.__reactInternalMemoizedMaskedChildContext;var i={},o;for(o in n)i[o]=t[o];return a&&(e=e.stateNode,e.__reactInternalMemoizedUnmaskedChildContext=t,e.__reactInternalMemoizedMaskedChildContext=i),i}function he(e){return e=e.childContextTypes,e!=null}function cr(){F(xe),F(pe)}function hl(e,t,n){if(pe.current!==St)throw Error(b(168));D(pe,t),D(xe,n)}function Bc(e,t,n){var a=e.stateNode;if(t=t.childContextTypes,typeof a.getChildContext!="function")return n;a=a.getChildContext();for(var i in a)if(!(i in t))throw Error(b(108,Bd(e)||"Unknown",i));return q({},n,a)}function pr(e){return e=(e=e.stateNode)&&e.__reactInternalMemoizedMergedChildContext||St,Dt=pe.current,D(pe,e),D(xe,xe.current),!0}function bl(e,t,n){var a=e.stateNode;if(!a)throw Error(b(169));n?(e=Bc(e,t,Dt),a.__reactInternalMemoizedMergedChildContext=e,F(xe),F(pe),D(pe,e)):F(xe),D(xe,n)}var Ke=null,Pr=!1,gi=!1;function Uc(e){Ke===null?Ke=[e]:Ke.push(e)}function nf(e){Pr=!0,Uc(e)}function Et(){if(!gi&&Ke!==null){gi=!0;var e=0,t=_;try{var n=Ke;for(_=1;e<n.length;e++){var a=n[e];do a=a(!0);while(a!==null)}Ke=null,Pr=!1}catch(i){throw Ke!==null&&(Ke=Ke.slice(e+1)),fc(zo,Et),i}finally{_=t,gi=!1}}return null}var en=[],tn=0,dr=null,ur=0,Me=[],Ee=0,Ot=null,Je=1,Ze="";function Tt(e,t){en[tn++]=ur,en[tn++]=dr,dr=e,ur=t}function Vc(e,t,n){Me[Ee++]=Je,Me[Ee++]=Ze,Me[Ee++]=Ot,Ot=e;var a=Je;e=Ze;var i=32-Ae(a)-1;a&=~(1<<i),n+=1;var o=32-Ae(t)+i;if(30<o){var s=i-i%5;o=(a&(1<<s)-1).toString(32),a>>=s,i-=s,Je=1<<32-Ae(t)+i|n<<i|a,Ze=o+e}else Je=1<<o|n<<i|a,Ze=e}function _o(e){e.return!==null&&(Tt(e,1),Vc(e,1,0))}function Io(e){for(;e===dr;)dr=en[--tn],en[tn]=null,ur=en[--tn],en[tn]=null;for(;e===Ot;)Ot=Me[--Ee],Me[Ee]=null,Ze=Me[--Ee],Me[Ee]=null,Je=Me[--Ee],Me[Ee]=null}var ke=null,we=null,B=!1,Oe=null;function Wc(e,t){var n=Pe(5,null,null,0);n.elementType="DELETED",n.stateNode=t,n.return=e,t=e.deletions,t===null?(e.deletions=[n],e.flags|=16):t.push(n)}function yl(e,t){switch(e.tag){case 5:var n=e.type;return t=t.nodeType!==1||n.toLowerCase()!==t.nodeName.toLowerCase()?null:t,t!==null?(e.stateNode=t,ke=e,we=bt(t.firstChild),!0):!1;case 6:return t=e.pendingProps===""||t.nodeType!==3?null:t,t!==null?(e.stateNode=t,ke=e,we=null,!0):!1;case 13:return t=t.nodeType!==8?null:t,t!==null?(n=Ot!==null?{id:Je,overflow:Ze}:null,e.memoizedState={dehydrated:t,treeContext:n,retryLane:1073741824},n=Pe(18,null,null,0),n.stateNode=t,n.return=e,e.child=n,ke=e,we=null,!0):!1;default:return!1}}function Ki(e){return(e.mode&1)!==0&&(e.flags&128)===0}function Ji(e){if(B){var t=we;if(t){var n=t;if(!yl(e,t)){if(Ki(e))throw Error(b(418));t=bt(n.nextSibling);var a=ke;t&&yl(e,t)?Wc(a,n):(e.flags=e.flags&-4097|2,B=!1,ke=e)}}else{if(Ki(e))throw Error(b(418));e.flags=e.flags&-4097|2,B=!1,ke=e}}}function wl(e){for(e=e.return;e!==null&&e.tag!==5&&e.tag!==3&&e.tag!==13;)e=e.return;ke=e}function Aa(e){if(e!==ke)return!1;if(!B)return wl(e),B=!0,!1;var t;if((t=e.tag!==3)&&!(t=e.tag!==5)&&(t=e.type,t=t!=="head"&&t!=="body"&&!qi(e.type,e.memoizedProps)),t&&(t=we)){if(Ki(e))throw Hc(),Error(b(418));for(;t;)Wc(e,t),t=bt(t.nextSibling)}if(wl(e),e.tag===13){if(e=e.memoizedState,e=e!==null?e.dehydrated:null,!e)throw Error(b(317));e:{for(e=e.nextSibling,t=0;e;){if(e.nodeType===8){var n=e.data;if(n==="/$"){if(t===0){we=bt(e.nextSibling);break e}t--}else n!=="$"&&n!=="$!"&&n!=="$?"||t++}e=e.nextSibling}we=null}}else we=ke?bt(e.stateNode.nextSibling):null;return!0}function Hc(){for(var e=we;e;)e=bt(e.nextSibling)}function mn(){we=ke=null,B=!1}function $o(e){Oe===null?Oe=[e]:Oe.push(e)}var af=it.ReactCurrentBatchConfig;function Tn(e,t,n){if(e=n.ref,e!==null&&typeof e!="function"&&typeof e!="object"){if(n._owner){if(n=n._owner,n){if(n.tag!==1)throw Error(b(309));var a=n.stateNode}if(!a)throw Error(b(147,e));var i=a,o=""+e;return t!==null&&t.ref!==null&&typeof t.ref=="function"&&t.ref._stringRef===o?t.ref:(t=function(s){var l=i.refs;s===null?delete l[o]:l[o]=s},t._stringRef=o,t)}if(typeof e!="string")throw Error(b(284));if(!n._owner)throw Error(b(290,e))}return e}function Fa(e,t){throw e=Object.prototype.toString.call(t),Error(b(31,e==="[object Object]"?"object with keys {"+Object.keys(t).join(", ")+"}":e))}function kl(e){var t=e._init;return t(e._payload)}function Yc(e){function t(u,d){if(e){var m=u.deletions;m===null?(u.deletions=[d],u.flags|=16):m.push(d)}}function n(u,d){if(!e)return null;for(;d!==null;)t(u,d),d=d.sibling;return null}function a(u,d){for(u=new Map;d!==null;)d.key!==null?u.set(d.key,d):u.set(d.index,d),d=d.sibling;return u}function i(u,d){return u=Nt(u,d),u.index=0,u.sibling=null,u}function o(u,d,m){return u.index=m,e?(m=u.alternate,m!==null?(m=m.index,m<d?(u.flags|=2,d):m):(u.flags|=2,d)):(u.flags|=1048576,d)}function s(u){return e&&u.alternate===null&&(u.flags|=2),u}function l(u,d,m,h){return d===null||d.tag!==6?(d=ki(m,u.mode,h),d.return=u,d):(d=i(d,m),d.return=u,d)}function c(u,d,m,h){var N=m.type;return N===Qt?f(u,d,m.props.children,h,m.key):d!==null&&(d.elementType===N||typeof N=="object"&&N!==null&&N.$$typeof===pt&&kl(N)===d.type)?(h=i(d,m.props),h.ref=Tn(u,d,m),h.return=u,h):(h=Za(m.type,m.key,m.props,null,u.mode,h),h.ref=Tn(u,d,m),h.return=u,h)}function p(u,d,m,h){return d===null||d.tag!==4||d.stateNode.containerInfo!==m.containerInfo||d.stateNode.implementation!==m.implementation?(d=Ni(m,u.mode,h),d.return=u,d):(d=i(d,m.children||[]),d.return=u,d)}function f(u,d,m,h,N){return d===null||d.tag!==7?(d=jt(m,u.mode,h,N),d.return=u,d):(d=i(d,m),d.return=u,d)}function g(u,d,m){if(typeof d=="string"&&d!==""||typeof d=="number")return d=ki(""+d,u.mode,m),d.return=u,d;if(typeof d=="object"&&d!==null){switch(d.$$typeof){case Sa:return m=Za(d.type,d.key,d.props,null,u.mode,m),m.ref=Tn(u,null,d),m.return=u,m;case qt:return d=Ni(d,u.mode,m),d.return=u,d;case pt:var h=d._init;return g(u,h(d._payload),m)}if($n(d)||Cn(d))return d=jt(d,u.mode,m,null),d.return=u,d;Fa(u,d)}return null}function v(u,d,m,h){var N=d!==null?d.key:null;if(typeof m=="string"&&m!==""||typeof m=="number")return N!==null?null:l(u,d,""+m,h);if(typeof m=="object"&&m!==null){switch(m.$$typeof){case Sa:return m.key===N?c(u,d,m,h):null;case qt:return m.key===N?p(u,d,m,h):null;case pt:return N=m._init,v(u,d,N(m._payload),h)}if($n(m)||Cn(m))return N!==null?null:f(u,d,m,h,null);Fa(u,m)}return null}function y(u,d,m,h,N){if(typeof h=="string"&&h!==""||typeof h=="number")return u=u.get(m)||null,l(d,u,""+h,N);if(typeof h=="object"&&h!==null){switch(h.$$typeof){case Sa:return u=u.get(h.key===null?m:h.key)||null,c(d,u,h,N);case qt:return u=u.get(h.key===null?m:h.key)||null,p(d,u,h,N);case pt:var C=h._init;return y(u,d,m,C(h._payload),N)}if($n(h)||Cn(h))return u=u.get(m)||null,f(d,u,h,N,null);Fa(d,h)}return null}function w(u,d,m,h){for(var N=null,C=null,M=d,E=d=0,Z=null;M!==null&&E<m.length;E++){M.index>E?(Z=M,M=null):Z=M.sibling;var L=v(u,M,m[E],h);if(L===null){M===null&&(M=Z);break}e&&M&&L.alternate===null&&t(u,M),d=o(L,d,E),C===null?N=L:C.sibling=L,C=L,M=Z}if(E===m.length)return n(u,M),B&&Tt(u,E),N;if(M===null){for(;E<m.length;E++)M=g(u,m[E],h),M!==null&&(d=o(M,d,E),C===null?N=M:C.sibling=M,C=M);return B&&Tt(u,E),N}for(M=a(u,M);E<m.length;E++)Z=y(M,u,E,m[E],h),Z!==null&&(e&&Z.alternate!==null&&M.delete(Z.key===null?E:Z.key),d=o(Z,d,E),C===null?N=Z:C.sibling=Z,C=Z);return e&&M.forEach(function(st){return t(u,st)}),B&&Tt(u,E),N}function k(u,d,m,h){var N=Cn(m);if(typeof N!="function")throw Error(b(150));if(m=N.call(m),m==null)throw Error(b(151));for(var C=N=null,M=d,E=d=0,Z=null,L=m.next();M!==null&&!L.done;E++,L=m.next()){M.index>E?(Z=M,M=null):Z=M.sibling;var st=v(u,M,L.value,h);if(st===null){M===null&&(M=Z);break}e&&M&&st.alternate===null&&t(u,M),d=o(st,d,E),C===null?N=st:C.sibling=st,C=st,M=Z}if(L.done)return n(u,M),B&&Tt(u,E),N;if(M===null){for(;!L.done;E++,L=m.next())L=g(u,L.value,h),L!==null&&(d=o(L,d,E),C===null?N=L:C.sibling=L,C=L);return B&&Tt(u,E),N}for(M=a(u,M);!L.done;E++,L=m.next())L=y(M,u,E,L.value,h),L!==null&&(e&&L.alternate!==null&&M.delete(L.key===null?E:L.key),d=o(L,d,E),C===null?N=L:C.sibling=L,C=L);return e&&M.forEach(function(ud){return t(u,ud)}),B&&Tt(u,E),N}function S(u,d,m,h){if(typeof m=="object"&&m!==null&&m.type===Qt&&m.key===null&&(m=m.props.children),typeof m=="object"&&m!==null){switch(m.$$typeof){case Sa:e:{for(var N=m.key,C=d;C!==null;){if(C.key===N){if(N=m.type,N===Qt){if(C.tag===7){n(u,C.sibling),d=i(C,m.props.children),d.return=u,u=d;break e}}else if(C.elementType===N||typeof N=="object"&&N!==null&&N.$$typeof===pt&&kl(N)===C.type){n(u,C.sibling),d=i(C,m.props),d.ref=Tn(u,C,m),d.return=u,u=d;break e}n(u,C);break}else t(u,C);C=C.sibling}m.type===Qt?(d=jt(m.props.children,u.mode,h,m.key),d.return=u,u=d):(h=Za(m.type,m.key,m.props,null,u.mode,h),h.ref=Tn(u,d,m),h.return=u,u=h)}return s(u);case qt:e:{for(C=m.key;d!==null;){if(d.key===C)if(d.tag===4&&d.stateNode.containerInfo===m.containerInfo&&d.stateNode.implementation===m.implementation){n(u,d.sibling),d=i(d,m.children||[]),d.return=u,u=d;break e}else{n(u,d);break}else t(u,d);d=d.sibling}d=Ni(m,u.mode,h),d.return=u,u=d}return s(u);case pt:return C=m._init,S(u,d,C(m._payload),h)}if($n(m))return w(u,d,m,h);if(Cn(m))return k(u,d,m,h);Fa(u,m)}return typeof m=="string"&&m!==""||typeof m=="number"?(m=""+m,d!==null&&d.tag===6?(n(u,d.sibling),d=i(d,m),d.return=u,u=d):(n(u,d),d=ki(m,u.mode,h),d.return=u,u=d),s(u)):n(u,d)}return S}var gn=Yc(!0),qc=Yc(!1),fr=Mt(null),mr=null,nn=null,jo=null;function Do(){jo=nn=mr=null}function Oo(e){var t=fr.current;F(fr),e._currentValue=t}function Zi(e,t,n){for(;e!==null;){var a=e.alternate;if((e.childLanes&t)!==t?(e.childLanes|=t,a!==null&&(a.childLanes|=t)):a!==null&&(a.childLanes&t)!==t&&(a.childLanes|=t),e===n)break;e=e.return}}function pn(e,t){mr=e,jo=nn=null,e=e.dependencies,e!==null&&e.firstContext!==null&&(e.lanes&t&&(ve=!0),e.firstContext=null)}function Re(e){var t=e._currentValue;if(jo!==e)if(e={context:e,memoizedValue:t,next:null},nn===null){if(mr===null)throw Error(b(308));nn=e,mr.dependencies={lanes:0,firstContext:e}}else nn=nn.next=e;return t}var _t=null;function Ao(e){_t===null?_t=[e]:_t.push(e)}function Qc(e,t,n,a){var i=t.interleaved;return i===null?(n.next=n,Ao(t)):(n.next=i.next,i.next=n),t.interleaved=n,at(e,a)}function at(e,t){e.lanes|=t;var n=e.alternate;for(n!==null&&(n.lanes|=t),n=e,e=e.return;e!==null;)e.childLanes|=t,n=e.alternate,n!==null&&(n.childLanes|=t),n=e,e=e.return;return n.tag===3?n.stateNode:null}var dt=!1;function Fo(e){e.updateQueue={baseState:e.memoizedState,firstBaseUpdate:null,lastBaseUpdate:null,shared:{pending:null,interleaved:null,lanes:0},effects:null}}function Gc(e,t){e=e.updateQueue,t.updateQueue===e&&(t.updateQueue={baseState:e.baseState,firstBaseUpdate:e.firstBaseUpdate,lastBaseUpdate:e.lastBaseUpdate,shared:e.shared,effects:e.effects})}function et(e,t){return{eventTime:e,lane:t,tag:0,payload:null,callback:null,next:null}}function yt(e,t,n){var a=e.updateQueue;if(a===null)return null;if(a=a.shared,T&2){var i=a.pending;return i===null?t.next=t:(t.next=i.next,i.next=t),a.pending=t,at(e,n)}return i=a.interleaved,i===null?(t.next=t,Ao(a)):(t.next=i.next,i.next=t),a.interleaved=t,at(e,n)}function qa(e,t,n){if(t=t.updateQueue,t!==null&&(t=t.shared,(n&4194240)!==0)){var a=t.lanes;a&=e.pendingLanes,n|=a,t.lanes=n,So(e,n)}}function Nl(e,t){var n=e.updateQueue,a=e.alternate;if(a!==null&&(a=a.updateQueue,n===a)){var i=null,o=null;if(n=n.firstBaseUpdate,n!==null){do{var s={eventTime:n.eventTime,lane:n.lane,tag:n.tag,payload:n.payload,callback:n.callback,next:null};o===null?i=o=s:o=o.next=s,n=n.next}while(n!==null);o===null?i=o=t:o=o.next=t}else i=o=t;n={baseState:a.baseState,firstBaseUpdate:i,lastBaseUpdate:o,shared:a.shared,effects:a.effects},e.updateQueue=n;return}e=n.lastBaseUpdate,e===null?n.firstBaseUpdate=t:e.next=t,n.lastBaseUpdate=t}function gr(e,t,n,a){var i=e.updateQueue;dt=!1;var o=i.firstBaseUpdate,s=i.lastBaseUpdate,l=i.shared.pending;if(l!==null){i.shared.pending=null;var c=l,p=c.next;c.next=null,s===null?o=p:s.next=p,s=c;var f=e.alternate;f!==null&&(f=f.updateQueue,l=f.lastBaseUpdate,l!==s&&(l===null?f.firstBaseUpdate=p:l.next=p,f.lastBaseUpdate=c))}if(o!==null){var g=i.baseState;s=0,f=p=c=null,l=o;do{var v=l.lane,y=l.eventTime;if((a&v)===v){f!==null&&(f=f.next={eventTime:y,lane:0,tag:l.tag,payload:l.payload,callback:l.callback,next:null});e:{var w=e,k=l;switch(v=t,y=n,k.tag){case 1:if(w=k.payload,typeof w=="function"){g=w.call(y,g,v);break e}g=w;break e;case 3:w.flags=w.flags&-65537|128;case 0:if(w=k.payload,v=typeof w=="function"?w.call(y,g,v):w,v==null)break e;g=q({},g,v);break e;case 2:dt=!0}}l.callback!==null&&l.lane!==0&&(e.flags|=64,v=i.effects,v===null?i.effects=[l]:v.push(l))}else y={eventTime:y,lane:v,tag:l.tag,payload:l.payload,callback:l.callback,next:null},f===null?(p=f=y,c=g):f=f.next=y,s|=v;if(l=l.next,l===null){if(l=i.shared.pending,l===null)break;v=l,l=v.next,v.next=null,i.lastBaseUpdate=v,i.shared.pending=null}}while(!0);if(f===null&&(c=g),i.baseState=c,i.firstBaseUpdate=p,i.lastBaseUpdate=f,t=i.shared.interleaved,t!==null){i=t;do s|=i.lane,i=i.next;while(i!==t)}else o===null&&(i.shared.lanes=0);Ft|=s,e.lanes=s,e.memoizedState=g}}function zl(e,t,n){if(e=t.effects,t.effects=null,e!==null)for(t=0;t<e.length;t++){var a=e[t],i=a.callback;if(i!==null){if(a.callback=null,a=n,typeof i!="function")throw Error(b(191,i));i.call(a)}}}var fa={},qe=Mt(fa),ra=Mt(fa),ia=Mt(fa);function It(e){if(e===fa)throw Error(b(174));return e}function Bo(e,t){switch(D(ia,t),D(ra,e),D(qe,fa),e=t.nodeType,e){case 9:case 11:t=(t=t.documentElement)?t.namespaceURI:_i(null,"");break;default:e=e===8?t.parentNode:t,t=e.namespaceURI||null,e=e.tagName,t=_i(t,e)}F(qe),D(qe,t)}function vn(){F(qe),F(ra),F(ia)}function Xc(e){It(ia.current);var t=It(qe.current),n=_i(t,e.type);t!==n&&(D(ra,e),D(qe,n))}function Uo(e){ra.current===e&&(F(qe),F(ra))}var H=Mt(0);function vr(e){for(var t=e;t!==null;){if(t.tag===13){var n=t.memoizedState;if(n!==null&&(n=n.dehydrated,n===null||n.data==="$?"||n.data==="$!"))return t}else if(t.tag===19&&t.memoizedProps.revealOrder!==void 0){if(t.flags&128)return t}else if(t.child!==null){t.child.return=t,t=t.child;continue}if(t===e)break;for(;t.sibling===null;){if(t.return===null||t.return===e)return null;t=t.return}t.sibling.return=t.return,t=t.sibling}return null}var vi=[];function Vo(){for(var e=0;e<vi.length;e++)vi[e]._workInProgressVersionPrimary=null;vi.length=0}var Qa=it.ReactCurrentDispatcher,xi=it.ReactCurrentBatchConfig,At=0,Y=null,K=null,ee=null,xr=!1,Vn=!1,oa=0,rf=0;function se(){throw Error(b(321))}function Wo(e,t){if(t===null)return!1;for(var n=0;n<t.length&&n<e.length;n++)if(!Be(e[n],t[n]))return!1;return!0}function Ho(e,t,n,a,i,o){if(At=o,Y=t,t.memoizedState=null,t.updateQueue=null,t.lanes=0,Qa.current=e===null||e.memoizedState===null?cf:pf,e=n(a,i),Vn){o=0;do{if(Vn=!1,oa=0,25<=o)throw Error(b(301));o+=1,ee=K=null,t.updateQueue=null,Qa.current=df,e=n(a,i)}while(Vn)}if(Qa.current=hr,t=K!==null&&K.next!==null,At=0,ee=K=Y=null,xr=!1,t)throw Error(b(300));return e}function Yo(){var e=oa!==0;return oa=0,e}function We(){var e={memoizedState:null,baseState:null,baseQueue:null,queue:null,next:null};return ee===null?Y.memoizedState=ee=e:ee=ee.next=e,ee}function Le(){if(K===null){var e=Y.alternate;e=e!==null?e.memoizedState:null}else e=K.next;var t=ee===null?Y.memoizedState:ee.next;if(t!==null)ee=t,K=e;else{if(e===null)throw Error(b(310));K=e,e={memoizedState:K.memoizedState,baseState:K.baseState,baseQueue:K.baseQueue,queue:K.queue,next:null},ee===null?Y.memoizedState=ee=e:ee=ee.next=e}return ee}function sa(e,t){return typeof t=="function"?t(e):t}function hi(e){var t=Le(),n=t.queue;if(n===null)throw Error(b(311));n.lastRenderedReducer=e;var a=K,i=a.baseQueue,o=n.pending;if(o!==null){if(i!==null){var s=i.next;i.next=o.next,o.next=s}a.baseQueue=i=o,n.pending=null}if(i!==null){o=i.next,a=a.baseState;var l=s=null,c=null,p=o;do{var f=p.lane;if((At&f)===f)c!==null&&(c=c.next={lane:0,action:p.action,hasEagerState:p.hasEagerState,eagerState:p.eagerState,next:null}),a=p.hasEagerState?p.eagerState:e(a,p.action);else{var g={lane:f,action:p.action,hasEagerState:p.hasEagerState,eagerState:p.eagerState,next:null};c===null?(l=c=g,s=a):c=c.next=g,Y.lanes|=f,Ft|=f}p=p.next}while(p!==null&&p!==o);c===null?s=a:c.next=l,Be(a,t.memoizedState)||(ve=!0),t.memoizedState=a,t.baseState=s,t.baseQueue=c,n.lastRenderedState=a}if(e=n.interleaved,e!==null){i=e;do o=i.lane,Y.lanes|=o,Ft|=o,i=i.next;while(i!==e)}else i===null&&(n.lanes=0);return[t.memoizedState,n.dispatch]}function bi(e){var t=Le(),n=t.queue;if(n===null)throw Error(b(311));n.lastRenderedReducer=e;var a=n.dispatch,i=n.pending,o=t.memoizedState;if(i!==null){n.pending=null;var s=i=i.next;do o=e(o,s.action),s=s.next;while(s!==i);Be(o,t.memoizedState)||(ve=!0),t.memoizedState=o,t.baseQueue===null&&(t.baseState=o),n.lastRenderedState=o}return[o,a]}function Kc(){}function Jc(e,t){var n=Y,a=Le(),i=t(),o=!Be(a.memoizedState,i);if(o&&(a.memoizedState=i,ve=!0),a=a.queue,qo(tp.bind(null,n,a,e),[e]),a.getSnapshot!==t||o||ee!==null&&ee.memoizedState.tag&1){if(n.flags|=2048,la(9,ep.bind(null,n,a,i,t),void 0,null),te===null)throw Error(b(349));At&30||Zc(n,t,i)}return i}function Zc(e,t,n){e.flags|=16384,e={getSnapshot:t,value:n},t=Y.updateQueue,t===null?(t={lastEffect:null,stores:null},Y.updateQueue=t,t.stores=[e]):(n=t.stores,n===null?t.stores=[e]:n.push(e))}function ep(e,t,n,a){t.value=n,t.getSnapshot=a,np(t)&&ap(e)}function tp(e,t,n){return n(function(){np(t)&&ap(e)})}function np(e){var t=e.getSnapshot;e=e.value;try{var n=t();return!Be(e,n)}catch{return!0}}function ap(e){var t=at(e,1);t!==null&&Fe(t,e,1,-1)}function Sl(e){var t=We();return typeof e=="function"&&(e=e()),t.memoizedState=t.baseState=e,e={pending:null,interleaved:null,lanes:0,dispatch:null,lastRenderedReducer:sa,lastRenderedState:e},t.queue=e,e=e.dispatch=lf.bind(null,Y,e),[t.memoizedState,e]}function la(e,t,n,a){return e={tag:e,create:t,destroy:n,deps:a,next:null},t=Y.updateQueue,t===null?(t={lastEffect:null,stores:null},Y.updateQueue=t,t.lastEffect=e.next=e):(n=t.lastEffect,n===null?t.lastEffect=e.next=e:(a=n.next,n.next=e,e.next=a,t.lastEffect=e)),e}function rp(){return Le().memoizedState}function Ga(e,t,n,a){var i=We();Y.flags|=e,i.memoizedState=la(1|t,n,void 0,a===void 0?null:a)}function Tr(e,t,n,a){var i=Le();a=a===void 0?null:a;var o=void 0;if(K!==null){var s=K.memoizedState;if(o=s.destroy,a!==null&&Wo(a,s.deps)){i.memoizedState=la(t,n,o,a);return}}Y.flags|=e,i.memoizedState=la(1|t,n,o,a)}function Cl(e,t){return Ga(8390656,8,e,t)}function qo(e,t){return Tr(2048,8,e,t)}function ip(e,t){return Tr(4,2,e,t)}function op(e,t){return Tr(4,4,e,t)}function sp(e,t){if(typeof t=="function")return e=e(),t(e),function(){t(null)};if(t!=null)return e=e(),t.current=e,function(){t.current=null}}function lp(e,t,n){return n=n!=null?n.concat([e]):null,Tr(4,4,sp.bind(null,t,e),n)}function Qo(){}function cp(e,t){var n=Le();t=t===void 0?null:t;var a=n.memoizedState;return a!==null&&t!==null&&Wo(t,a[1])?a[0]:(n.memoizedState=[e,t],e)}function pp(e,t){var n=Le();t=t===void 0?null:t;var a=n.memoizedState;return a!==null&&t!==null&&Wo(t,a[1])?a[0]:(e=e(),n.memoizedState=[e,t],e)}function dp(e,t,n){return At&21?(Be(n,t)||(n=vc(),Y.lanes|=n,Ft|=n,e.baseState=!0),t):(e.baseState&&(e.baseState=!1,ve=!0),e.memoizedState=n)}function of(e,t){var n=_;_=n!==0&&4>n?n:4,e(!0);var a=xi.transition;xi.transition={};try{e(!1),t()}finally{_=n,xi.transition=a}}function up(){return Le().memoizedState}function sf(e,t,n){var a=kt(e);if(n={lane:a,action:n,hasEagerState:!1,eagerState:null,next:null},fp(e))mp(t,n);else if(n=Qc(e,t,n,a),n!==null){var i=fe();Fe(n,e,a,i),gp(n,t,a)}}function lf(e,t,n){var a=kt(e),i={lane:a,action:n,hasEagerState:!1,eagerState:null,next:null};if(fp(e))mp(t,i);else{var o=e.alternate;if(e.lanes===0&&(o===null||o.lanes===0)&&(o=t.lastRenderedReducer,o!==null))try{var s=t.lastRenderedState,l=o(s,n);if(i.hasEagerState=!0,i.eagerState=l,Be(l,s)){var c=t.interleaved;c===null?(i.next=i,Ao(t)):(i.next=c.next,c.next=i),t.interleaved=i;return}}catch{}finally{}n=Qc(e,t,i,a),n!==null&&(i=fe(),Fe(n,e,a,i),gp(n,t,a))}}function fp(e){var t=e.alternate;return e===Y||t!==null&&t===Y}function mp(e,t){Vn=xr=!0;var n=e.pending;n===null?t.next=t:(t.next=n.next,n.next=t),e.pending=t}function gp(e,t,n){if(n&4194240){var a=t.lanes;a&=e.pendingLanes,n|=a,t.lanes=n,So(e,n)}}var hr={readContext:Re,useCallback:se,useContext:se,useEffect:se,useImperativeHandle:se,useInsertionEffect:se,useLayoutEffect:se,useMemo:se,useReducer:se,useRef:se,useState:se,useDebugValue:se,useDeferredValue:se,useTransition:se,useMutableSource:se,useSyncExternalStore:se,useId:se,unstable_isNewReconciler:!1},cf={readContext:Re,useCallback:function(e,t){return We().memoizedState=[e,t===void 0?null:t],e},useContext:Re,useEffect:Cl,useImperativeHandle:function(e,t,n){return n=n!=null?n.concat([e]):null,Ga(4194308,4,sp.bind(null,t,e),n)},useLayoutEffect:function(e,t){return Ga(4194308,4,e,t)},useInsertionEffect:function(e,t){return Ga(4,2,e,t)},useMemo:function(e,t){var n=We();return t=t===void 0?null:t,e=e(),n.memoizedState=[e,t],e},useReducer:function(e,t,n){var a=We();return t=n!==void 0?n(t):t,a.memoizedState=a.baseState=t,e={pending:null,interleaved:null,lanes:0,dispatch:null,lastRenderedReducer:e,lastRenderedState:t},a.queue=e,e=e.dispatch=sf.bind(null,Y,e),[a.memoizedState,e]},useRef:function(e){var t=We();return e={current:e},t.memoizedState=e},useState:Sl,useDebugValue:Qo,useDeferredValue:function(e){return We().memoizedState=e},useTransition:function(){var e=Sl(!1),t=e[0];return e=of.bind(null,e[1]),We().memoizedState=e,[t,e]},useMutableSource:function(){},useSyncExternalStore:function(e,t,n){var a=Y,i=We();if(B){if(n===void 0)throw Error(b(407));n=n()}else{if(n=t(),te===null)throw Error(b(349));At&30||Zc(a,t,n)}i.memoizedState=n;var o={value:n,getSnapshot:t};return i.queue=o,Cl(tp.bind(null,a,o,e),[e]),a.flags|=2048,la(9,ep.bind(null,a,o,n,t),void 0,null),n},useId:function(){var e=We(),t=te.identifierPrefix;if(B){var n=Ze,a=Je;n=(a&~(1<<32-Ae(a)-1)).toString(32)+n,t=":"+t+"R"+n,n=oa++,0<n&&(t+="H"+n.toString(32)),t+=":"}else n=rf++,t=":"+t+"r"+n.toString(32)+":";return e.memoizedState=t},unstable_isNewReconciler:!1},pf={readContext:Re,useCallback:cp,useContext:Re,useEffect:qo,useImperativeHandle:lp,useInsertionEffect:ip,useLayoutEffect:op,useMemo:pp,useReducer:hi,useRef:rp,useState:function(){return hi(sa)},useDebugValue:Qo,useDeferredValue:function(e){var t=Le();return dp(t,K.memoizedState,e)},useTransition:function(){var e=hi(sa)[0],t=Le().memoizedState;return[e,t]},useMutableSource:Kc,useSyncExternalStore:Jc,useId:up,unstable_isNewReconciler:!1},df={readContext:Re,useCallback:cp,useContext:Re,useEffect:qo,useImperativeHandle:lp,useInsertionEffect:ip,useLayoutEffect:op,useMemo:pp,useReducer:bi,useRef:rp,useState:function(){return bi(sa)},useDebugValue:Qo,useDeferredValue:function(e){var t=Le();return K===null?t.memoizedState=e:dp(t,K.memoizedState,e)},useTransition:function(){var e=bi(sa)[0],t=Le().memoizedState;return[e,t]},useMutableSource:Kc,useSyncExternalStore:Jc,useId:up,unstable_isNewReconciler:!1};function je(e,t){if(e&&e.defaultProps){t=q({},t),e=e.defaultProps;for(var n in e)t[n]===void 0&&(t[n]=e[n]);return t}return t}function eo(e,t,n,a){t=e.memoizedState,n=n(a,t),n=n==null?t:q({},t,n),e.memoizedState=n,e.lanes===0&&(e.updateQueue.baseState=n)}var Rr={isMounted:function(e){return(e=e._reactInternals)?Vt(e)===e:!1},enqueueSetState:function(e,t,n){e=e._reactInternals;var a=fe(),i=kt(e),o=et(a,i);o.payload=t,n!=null&&(o.callback=n),t=yt(e,o,i),t!==null&&(Fe(t,e,i,a),qa(t,e,i))},enqueueReplaceState:function(e,t,n){e=e._reactInternals;var a=fe(),i=kt(e),o=et(a,i);o.tag=1,o.payload=t,n!=null&&(o.callback=n),t=yt(e,o,i),t!==null&&(Fe(t,e,i,a),qa(t,e,i))},enqueueForceUpdate:function(e,t){e=e._reactInternals;var n=fe(),a=kt(e),i=et(n,a);i.tag=2,t!=null&&(i.callback=t),t=yt(e,i,a),t!==null&&(Fe(t,e,a,n),qa(t,e,a))}};function Ml(e,t,n,a,i,o,s){return e=e.stateNode,typeof e.shouldComponentUpdate=="function"?e.shouldComponentUpdate(a,o,s):t.prototype&&t.prototype.isPureReactComponent?!ea(n,a)||!ea(i,o):!0}function vp(e,t,n){var a=!1,i=St,o=t.contextType;return typeof o=="object"&&o!==null?o=Re(o):(i=he(t)?Dt:pe.current,a=t.contextTypes,o=(a=a!=null)?fn(e,i):St),t=new t(n,o),e.memoizedState=t.state!==null&&t.state!==void 0?t.state:null,t.updater=Rr,e.stateNode=t,t._reactInternals=e,a&&(e=e.stateNode,e.__reactInternalMemoizedUnmaskedChildContext=i,e.__reactInternalMemoizedMaskedChildContext=o),t}function El(e,t,n,a){e=t.state,typeof t.componentWillReceiveProps=="function"&&t.componentWillReceiveProps(n,a),typeof t.UNSAFE_componentWillReceiveProps=="function"&&t.UNSAFE_componentWillReceiveProps(n,a),t.state!==e&&Rr.enqueueReplaceState(t,t.state,null)}function to(e,t,n,a){var i=e.stateNode;i.props=n,i.state=e.memoizedState,i.refs={},Fo(e);var o=t.contextType;typeof o=="object"&&o!==null?i.context=Re(o):(o=he(t)?Dt:pe.current,i.context=fn(e,o)),i.state=e.memoizedState,o=t.getDerivedStateFromProps,typeof o=="function"&&(eo(e,t,o,n),i.state=e.memoizedState),typeof t.getDerivedStateFromProps=="function"||typeof i.getSnapshotBeforeUpdate=="function"||typeof i.UNSAFE_componentWillMount!="function"&&typeof i.componentWillMount!="function"||(t=i.state,typeof i.componentWillMount=="function"&&i.componentWillMount(),typeof i.UNSAFE_componentWillMount=="function"&&i.UNSAFE_componentWillMount(),t!==i.state&&Rr.enqueueReplaceState(i,i.state,null),gr(e,n,i,a),i.state=e.memoizedState),typeof i.componentDidMount=="function"&&(e.flags|=4194308)}function xn(e,t){try{var n="",a=t;do n+=Fd(a),a=a.return;while(a);var i=n}catch(o){i=`
Error generating stack: `+o.message+`
`+o.stack}return{value:e,source:t,stack:i,digest:null}}function yi(e,t,n){return{value:e,source:null,stack:n??null,digest:t??null}}function no(e,t){try{console.error(t.value)}catch(n){setTimeout(function(){throw n})}}var uf=typeof WeakMap=="function"?WeakMap:Map;function xp(e,t,n){n=et(-1,n),n.tag=3,n.payload={element:null};var a=t.value;return n.callback=function(){yr||(yr=!0,fo=a),no(e,t)},n}function hp(e,t,n){n=et(-1,n),n.tag=3;var a=e.type.getDerivedStateFromError;if(typeof a=="function"){var i=t.value;n.payload=function(){return a(i)},n.callback=function(){no(e,t)}}var o=e.stateNode;return o!==null&&typeof o.componentDidCatch=="function"&&(n.callback=function(){no(e,t),typeof a!="function"&&(wt===null?wt=new Set([this]):wt.add(this));var s=t.stack;this.componentDidCatch(t.value,{componentStack:s!==null?s:""})}),n}function Pl(e,t,n){var a=e.pingCache;if(a===null){a=e.pingCache=new uf;var i=new Set;a.set(t,i)}else i=a.get(t),i===void 0&&(i=new Set,a.set(t,i));i.has(n)||(i.add(n),e=Cf.bind(null,e,t,n),t.then(e,e))}function Tl(e){do{var t;if((t=e.tag===13)&&(t=e.memoizedState,t=t!==null?t.dehydrated!==null:!0),t)return e;e=e.return}while(e!==null);return null}function Rl(e,t,n,a,i){return e.mode&1?(e.flags|=65536,e.lanes=i,e):(e===t?e.flags|=65536:(e.flags|=128,n.flags|=131072,n.flags&=-52805,n.tag===1&&(n.alternate===null?n.tag=17:(t=et(-1,1),t.tag=2,yt(n,t,1))),n.lanes|=1),e)}var ff=it.ReactCurrentOwner,ve=!1;function ue(e,t,n,a){t.child=e===null?qc(t,null,n,a):gn(t,e.child,n,a)}function Ll(e,t,n,a,i){n=n.render;var o=t.ref;return pn(t,i),a=Ho(e,t,n,a,o,i),n=Yo(),e!==null&&!ve?(t.updateQueue=e.updateQueue,t.flags&=-2053,e.lanes&=~i,rt(e,t,i)):(B&&n&&_o(t),t.flags|=1,ue(e,t,a,i),t.child)}function _l(e,t,n,a,i){if(e===null){var o=n.type;return typeof o=="function"&&!ns(o)&&o.defaultProps===void 0&&n.compare===null&&n.defaultProps===void 0?(t.tag=15,t.type=o,bp(e,t,o,a,i)):(e=Za(n.type,null,a,t,t.mode,i),e.ref=t.ref,e.return=t,t.child=e)}if(o=e.child,!(e.lanes&i)){var s=o.memoizedProps;if(n=n.compare,n=n!==null?n:ea,n(s,a)&&e.ref===t.ref)return rt(e,t,i)}return t.flags|=1,e=Nt(o,a),e.ref=t.ref,e.return=t,t.child=e}function bp(e,t,n,a,i){if(e!==null){var o=e.memoizedProps;if(ea(o,a)&&e.ref===t.ref)if(ve=!1,t.pendingProps=a=o,(e.lanes&i)!==0)e.flags&131072&&(ve=!0);else return t.lanes=e.lanes,rt(e,t,i)}return ao(e,t,n,a,i)}function yp(e,t,n){var a=t.pendingProps,i=a.children,o=e!==null?e.memoizedState:null;if(a.mode==="hidden")if(!(t.mode&1))t.memoizedState={baseLanes:0,cachePool:null,transitions:null},D(rn,ye),ye|=n;else{if(!(n&1073741824))return e=o!==null?o.baseLanes|n:n,t.lanes=t.childLanes=1073741824,t.memoizedState={baseLanes:e,cachePool:null,transitions:null},t.updateQueue=null,D(rn,ye),ye|=e,null;t.memoizedState={baseLanes:0,cachePool:null,transitions:null},a=o!==null?o.baseLanes:n,D(rn,ye),ye|=a}else o!==null?(a=o.baseLanes|n,t.memoizedState=null):a=n,D(rn,ye),ye|=a;return ue(e,t,i,n),t.child}function wp(e,t){var n=t.ref;(e===null&&n!==null||e!==null&&e.ref!==n)&&(t.flags|=512,t.flags|=2097152)}function ao(e,t,n,a,i){var o=he(n)?Dt:pe.current;return o=fn(t,o),pn(t,i),n=Ho(e,t,n,a,o,i),a=Yo(),e!==null&&!ve?(t.updateQueue=e.updateQueue,t.flags&=-2053,e.lanes&=~i,rt(e,t,i)):(B&&a&&_o(t),t.flags|=1,ue(e,t,n,i),t.child)}function Il(e,t,n,a,i){if(he(n)){var o=!0;pr(t)}else o=!1;if(pn(t,i),t.stateNode===null)Xa(e,t),vp(t,n,a),to(t,n,a,i),a=!0;else if(e===null){var s=t.stateNode,l=t.memoizedProps;s.props=l;var c=s.context,p=n.contextType;typeof p=="object"&&p!==null?p=Re(p):(p=he(n)?Dt:pe.current,p=fn(t,p));var f=n.getDerivedStateFromProps,g=typeof f=="function"||typeof s.getSnapshotBeforeUpdate=="function";g||typeof s.UNSAFE_componentWillReceiveProps!="function"&&typeof s.componentWillReceiveProps!="function"||(l!==a||c!==p)&&El(t,s,a,p),dt=!1;var v=t.memoizedState;s.state=v,gr(t,a,s,i),c=t.memoizedState,l!==a||v!==c||xe.current||dt?(typeof f=="function"&&(eo(t,n,f,a),c=t.memoizedState),(l=dt||Ml(t,n,l,a,v,c,p))?(g||typeof s.UNSAFE_componentWillMount!="function"&&typeof s.componentWillMount!="function"||(typeof s.componentWillMount=="function"&&s.componentWillMount(),typeof s.UNSAFE_componentWillMount=="function"&&s.UNSAFE_componentWillMount()),typeof s.componentDidMount=="function"&&(t.flags|=4194308)):(typeof s.componentDidMount=="function"&&(t.flags|=4194308),t.memoizedProps=a,t.memoizedState=c),s.props=a,s.state=c,s.context=p,a=l):(typeof s.componentDidMount=="function"&&(t.flags|=4194308),a=!1)}else{s=t.stateNode,Gc(e,t),l=t.memoizedProps,p=t.type===t.elementType?l:je(t.type,l),s.props=p,g=t.pendingProps,v=s.context,c=n.contextType,typeof c=="object"&&c!==null?c=Re(c):(c=he(n)?Dt:pe.current,c=fn(t,c));var y=n.getDerivedStateFromProps;(f=typeof y=="function"||typeof s.getSnapshotBeforeUpdate=="function")||typeof s.UNSAFE_componentWillReceiveProps!="function"&&typeof s.componentWillReceiveProps!="function"||(l!==g||v!==c)&&El(t,s,a,c),dt=!1,v=t.memoizedState,s.state=v,gr(t,a,s,i);var w=t.memoizedState;l!==g||v!==w||xe.current||dt?(typeof y=="function"&&(eo(t,n,y,a),w=t.memoizedState),(p=dt||Ml(t,n,p,a,v,w,c)||!1)?(f||typeof s.UNSAFE_componentWillUpdate!="function"&&typeof s.componentWillUpdate!="function"||(typeof s.componentWillUpdate=="function"&&s.componentWillUpdate(a,w,c),typeof s.UNSAFE_componentWillUpdate=="function"&&s.UNSAFE_componentWillUpdate(a,w,c)),typeof s.componentDidUpdate=="function"&&(t.flags|=4),typeof s.getSnapshotBeforeUpdate=="function"&&(t.flags|=1024)):(typeof s.componentDidUpdate!="function"||l===e.memoizedProps&&v===e.memoizedState||(t.flags|=4),typeof s.getSnapshotBeforeUpdate!="function"||l===e.memoizedProps&&v===e.memoizedState||(t.flags|=1024),t.memoizedProps=a,t.memoizedState=w),s.props=a,s.state=w,s.context=c,a=p):(typeof s.componentDidUpdate!="function"||l===e.memoizedProps&&v===e.memoizedState||(t.flags|=4),typeof s.getSnapshotBeforeUpdate!="function"||l===e.memoizedProps&&v===e.memoizedState||(t.flags|=1024),a=!1)}return ro(e,t,n,a,o,i)}function ro(e,t,n,a,i,o){wp(e,t);var s=(t.flags&128)!==0;if(!a&&!s)return i&&bl(t,n,!1),rt(e,t,o);a=t.stateNode,ff.current=t;var l=s&&typeof n.getDerivedStateFromError!="function"?null:a.render();return t.flags|=1,e!==null&&s?(t.child=gn(t,e.child,null,o),t.child=gn(t,null,l,o)):ue(e,t,l,o),t.memoizedState=a.state,i&&bl(t,n,!0),t.child}function kp(e){var t=e.stateNode;t.pendingContext?hl(e,t.pendingContext,t.pendingContext!==t.context):t.context&&hl(e,t.context,!1),Bo(e,t.containerInfo)}function $l(e,t,n,a,i){return mn(),$o(i),t.flags|=256,ue(e,t,n,a),t.child}var io={dehydrated:null,treeContext:null,retryLane:0};function oo(e){return{baseLanes:e,cachePool:null,transitions:null}}function Np(e,t,n){var a=t.pendingProps,i=H.current,o=!1,s=(t.flags&128)!==0,l;if((l=s)||(l=e!==null&&e.memoizedState===null?!1:(i&2)!==0),l?(o=!0,t.flags&=-129):(e===null||e.memoizedState!==null)&&(i|=1),D(H,i&1),e===null)return Ji(t),e=t.memoizedState,e!==null&&(e=e.dehydrated,e!==null)?(t.mode&1?e.data==="$!"?t.lanes=8:t.lanes=1073741824:t.lanes=1,null):(s=a.children,e=a.fallback,o?(a=t.mode,o=t.child,s={mode:"hidden",children:s},!(a&1)&&o!==null?(o.childLanes=0,o.pendingProps=s):o=Ir(s,a,0,null),e=jt(e,a,n,null),o.return=t,e.return=t,o.sibling=e,t.child=o,t.child.memoizedState=oo(n),t.memoizedState=io,e):Go(t,s));if(i=e.memoizedState,i!==null&&(l=i.dehydrated,l!==null))return mf(e,t,s,a,l,i,n);if(o){o=a.fallback,s=t.mode,i=e.child,l=i.sibling;var c={mode:"hidden",children:a.children};return!(s&1)&&t.child!==i?(a=t.child,a.childLanes=0,a.pendingProps=c,t.deletions=null):(a=Nt(i,c),a.subtreeFlags=i.subtreeFlags&14680064),l!==null?o=Nt(l,o):(o=jt(o,s,n,null),o.flags|=2),o.return=t,a.return=t,a.sibling=o,t.child=a,a=o,o=t.child,s=e.child.memoizedState,s=s===null?oo(n):{baseLanes:s.baseLanes|n,cachePool:null,transitions:s.transitions},o.memoizedState=s,o.childLanes=e.childLanes&~n,t.memoizedState=io,a}return o=e.child,e=o.sibling,a=Nt(o,{mode:"visible",children:a.children}),!(t.mode&1)&&(a.lanes=n),a.return=t,a.sibling=null,e!==null&&(n=t.deletions,n===null?(t.deletions=[e],t.flags|=16):n.push(e)),t.child=a,t.memoizedState=null,a}function Go(e,t){return t=Ir({mode:"visible",children:t},e.mode,0,null),t.return=e,e.child=t}function Ba(e,t,n,a){return a!==null&&$o(a),gn(t,e.child,null,n),e=Go(t,t.pendingProps.children),e.flags|=2,t.memoizedState=null,e}function mf(e,t,n,a,i,o,s){if(n)return t.flags&256?(t.flags&=-257,a=yi(Error(b(422))),Ba(e,t,s,a)):t.memoizedState!==null?(t.child=e.child,t.flags|=128,null):(o=a.fallback,i=t.mode,a=Ir({mode:"visible",children:a.children},i,0,null),o=jt(o,i,s,null),o.flags|=2,a.return=t,o.return=t,a.sibling=o,t.child=a,t.mode&1&&gn(t,e.child,null,s),t.child.memoizedState=oo(s),t.memoizedState=io,o);if(!(t.mode&1))return Ba(e,t,s,null);if(i.data==="$!"){if(a=i.nextSibling&&i.nextSibling.dataset,a)var l=a.dgst;return a=l,o=Error(b(419)),a=yi(o,a,void 0),Ba(e,t,s,a)}if(l=(s&e.childLanes)!==0,ve||l){if(a=te,a!==null){switch(s&-s){case 4:i=2;break;case 16:i=8;break;case 64:case 128:case 256:case 512:case 1024:case 2048:case 4096:case 8192:case 16384:case 32768:case 65536:case 131072:case 262144:case 524288:case 1048576:case 2097152:case 4194304:case 8388608:case 16777216:case 33554432:case 67108864:i=32;break;case 536870912:i=268435456;break;default:i=0}i=i&(a.suspendedLanes|s)?0:i,i!==0&&i!==o.retryLane&&(o.retryLane=i,at(e,i),Fe(a,e,i,-1))}return ts(),a=yi(Error(b(421))),Ba(e,t,s,a)}return i.data==="$?"?(t.flags|=128,t.child=e.child,t=Mf.bind(null,e),i._reactRetry=t,null):(e=o.treeContext,we=bt(i.nextSibling),ke=t,B=!0,Oe=null,e!==null&&(Me[Ee++]=Je,Me[Ee++]=Ze,Me[Ee++]=Ot,Je=e.id,Ze=e.overflow,Ot=t),t=Go(t,a.children),t.flags|=4096,t)}function jl(e,t,n){e.lanes|=t;var a=e.alternate;a!==null&&(a.lanes|=t),Zi(e.return,t,n)}function wi(e,t,n,a,i){var o=e.memoizedState;o===null?e.memoizedState={isBackwards:t,rendering:null,renderingStartTime:0,last:a,tail:n,tailMode:i}:(o.isBackwards=t,o.rendering=null,o.renderingStartTime=0,o.last=a,o.tail=n,o.tailMode=i)}function zp(e,t,n){var a=t.pendingProps,i=a.revealOrder,o=a.tail;if(ue(e,t,a.children,n),a=H.current,a&2)a=a&1|2,t.flags|=128;else{if(e!==null&&e.flags&128)e:for(e=t.child;e!==null;){if(e.tag===13)e.memoizedState!==null&&jl(e,n,t);else if(e.tag===19)jl(e,n,t);else if(e.child!==null){e.child.return=e,e=e.child;continue}if(e===t)break e;for(;e.sibling===null;){if(e.return===null||e.return===t)break e;e=e.return}e.sibling.return=e.return,e=e.sibling}a&=1}if(D(H,a),!(t.mode&1))t.memoizedState=null;else switch(i){case"forwards":for(n=t.child,i=null;n!==null;)e=n.alternate,e!==null&&vr(e)===null&&(i=n),n=n.sibling;n=i,n===null?(i=t.child,t.child=null):(i=n.sibling,n.sibling=null),wi(t,!1,i,n,o);break;case"backwards":for(n=null,i=t.child,t.child=null;i!==null;){if(e=i.alternate,e!==null&&vr(e)===null){t.child=i;break}e=i.sibling,i.sibling=n,n=i,i=e}wi(t,!0,n,null,o);break;case"together":wi(t,!1,null,null,void 0);break;default:t.memoizedState=null}return t.child}function Xa(e,t){!(t.mode&1)&&e!==null&&(e.alternate=null,t.alternate=null,t.flags|=2)}function rt(e,t,n){if(e!==null&&(t.dependencies=e.dependencies),Ft|=t.lanes,!(n&t.childLanes))return null;if(e!==null&&t.child!==e.child)throw Error(b(153));if(t.child!==null){for(e=t.child,n=Nt(e,e.pendingProps),t.child=n,n.return=t;e.sibling!==null;)e=e.sibling,n=n.sibling=Nt(e,e.pendingProps),n.return=t;n.sibling=null}return t.child}function gf(e,t,n){switch(t.tag){case 3:kp(t),mn();break;case 5:Xc(t);break;case 1:he(t.type)&&pr(t);break;case 4:Bo(t,t.stateNode.containerInfo);break;case 10:var a=t.type._context,i=t.memoizedProps.value;D(fr,a._currentValue),a._currentValue=i;break;case 13:if(a=t.memoizedState,a!==null)return a.dehydrated!==null?(D(H,H.current&1),t.flags|=128,null):n&t.child.childLanes?Np(e,t,n):(D(H,H.current&1),e=rt(e,t,n),e!==null?e.sibling:null);D(H,H.current&1);break;case 19:if(a=(n&t.childLanes)!==0,e.flags&128){if(a)return zp(e,t,n);t.flags|=128}if(i=t.memoizedState,i!==null&&(i.rendering=null,i.tail=null,i.lastEffect=null),D(H,H.current),a)break;return null;case 22:case 23:return t.lanes=0,yp(e,t,n)}return rt(e,t,n)}var Sp,so,Cp,Mp;Sp=function(e,t){for(var n=t.child;n!==null;){if(n.tag===5||n.tag===6)e.appendChild(n.stateNode);else if(n.tag!==4&&n.child!==null){n.child.return=n,n=n.child;continue}if(n===t)break;for(;n.sibling===null;){if(n.return===null||n.return===t)return;n=n.return}n.sibling.return=n.return,n=n.sibling}};so=function(){};Cp=function(e,t,n,a){var i=e.memoizedProps;if(i!==a){e=t.stateNode,It(qe.current);var o=null;switch(n){case"input":i=Pi(e,i),a=Pi(e,a),o=[];break;case"select":i=q({},i,{value:void 0}),a=q({},a,{value:void 0}),o=[];break;case"textarea":i=Li(e,i),a=Li(e,a),o=[];break;default:typeof i.onClick!="function"&&typeof a.onClick=="function"&&(e.onclick=lr)}Ii(n,a);var s;n=null;for(p in i)if(!a.hasOwnProperty(p)&&i.hasOwnProperty(p)&&i[p]!=null)if(p==="style"){var l=i[p];for(s in l)l.hasOwnProperty(s)&&(n||(n={}),n[s]="")}else p!=="dangerouslySetInnerHTML"&&p!=="children"&&p!=="suppressContentEditableWarning"&&p!=="suppressHydrationWarning"&&p!=="autoFocus"&&(qn.hasOwnProperty(p)?o||(o=[]):(o=o||[]).push(p,null));for(p in a){var c=a[p];if(l=i?.[p],a.hasOwnProperty(p)&&c!==l&&(c!=null||l!=null))if(p==="style")if(l){for(s in l)!l.hasOwnProperty(s)||c&&c.hasOwnProperty(s)||(n||(n={}),n[s]="");for(s in c)c.hasOwnProperty(s)&&l[s]!==c[s]&&(n||(n={}),n[s]=c[s])}else n||(o||(o=[]),o.push(p,n)),n=c;else p==="dangerouslySetInnerHTML"?(c=c?c.__html:void 0,l=l?l.__html:void 0,c!=null&&l!==c&&(o=o||[]).push(p,c)):p==="children"?typeof c!="string"&&typeof c!="number"||(o=o||[]).push(p,""+c):p!=="suppressContentEditableWarning"&&p!=="suppressHydrationWarning"&&(qn.hasOwnProperty(p)?(c!=null&&p==="onScroll"&&A("scroll",e),o||l===c||(o=[])):(o=o||[]).push(p,c))}n&&(o=o||[]).push("style",n);var p=o;(t.updateQueue=p)&&(t.flags|=4)}};Mp=function(e,t,n,a){n!==a&&(t.flags|=4)};function Rn(e,t){if(!B)switch(e.tailMode){case"hidden":t=e.tail;for(var n=null;t!==null;)t.alternate!==null&&(n=t),t=t.sibling;n===null?e.tail=null:n.sibling=null;break;case"collapsed":n=e.tail;for(var a=null;n!==null;)n.alternate!==null&&(a=n),n=n.sibling;a===null?t||e.tail===null?e.tail=null:e.tail.sibling=null:a.sibling=null}}function le(e){var t=e.alternate!==null&&e.alternate.child===e.child,n=0,a=0;if(t)for(var i=e.child;i!==null;)n|=i.lanes|i.childLanes,a|=i.subtreeFlags&14680064,a|=i.flags&14680064,i.return=e,i=i.sibling;else for(i=e.child;i!==null;)n|=i.lanes|i.childLanes,a|=i.subtreeFlags,a|=i.flags,i.return=e,i=i.sibling;return e.subtreeFlags|=a,e.childLanes=n,t}function vf(e,t,n){var a=t.pendingProps;switch(Io(t),t.tag){case 2:case 16:case 15:case 0:case 11:case 7:case 8:case 12:case 9:case 14:return le(t),null;case 1:return he(t.type)&&cr(),le(t),null;case 3:return a=t.stateNode,vn(),F(xe),F(pe),Vo(),a.pendingContext&&(a.context=a.pendingContext,a.pendingContext=null),(e===null||e.child===null)&&(Aa(t)?t.flags|=4:e===null||e.memoizedState.isDehydrated&&!(t.flags&256)||(t.flags|=1024,Oe!==null&&(vo(Oe),Oe=null))),so(e,t),le(t),null;case 5:Uo(t);var i=It(ia.current);if(n=t.type,e!==null&&t.stateNode!=null)Cp(e,t,n,a,i),e.ref!==t.ref&&(t.flags|=512,t.flags|=2097152);else{if(!a){if(t.stateNode===null)throw Error(b(166));return le(t),null}if(e=It(qe.current),Aa(t)){a=t.stateNode,n=t.type;var o=t.memoizedProps;switch(a[He]=t,a[aa]=o,e=(t.mode&1)!==0,n){case"dialog":A("cancel",a),A("close",a);break;case"iframe":case"object":case"embed":A("load",a);break;case"video":case"audio":for(i=0;i<Dn.length;i++)A(Dn[i],a);break;case"source":A("error",a);break;case"img":case"image":case"link":A("error",a),A("load",a);break;case"details":A("toggle",a);break;case"input":Ws(a,o),A("invalid",a);break;case"select":a._wrapperState={wasMultiple:!!o.multiple},A("invalid",a);break;case"textarea":Ys(a,o),A("invalid",a)}Ii(n,o),i=null;for(var s in o)if(o.hasOwnProperty(s)){var l=o[s];s==="children"?typeof l=="string"?a.textContent!==l&&(o.suppressHydrationWarning!==!0&&Oa(a.textContent,l,e),i=["children",l]):typeof l=="number"&&a.textContent!==""+l&&(o.suppressHydrationWarning!==!0&&Oa(a.textContent,l,e),i=["children",""+l]):qn.hasOwnProperty(s)&&l!=null&&s==="onScroll"&&A("scroll",a)}switch(n){case"input":Ca(a),Hs(a,o,!0);break;case"textarea":Ca(a),qs(a);break;case"select":case"option":break;default:typeof o.onClick=="function"&&(a.onclick=lr)}a=i,t.updateQueue=a,a!==null&&(t.flags|=4)}else{s=i.nodeType===9?i:i.ownerDocument,e==="http://www.w3.org/1999/xhtml"&&(e=tc(n)),e==="http://www.w3.org/1999/xhtml"?n==="script"?(e=s.createElement("div"),e.innerHTML="<script><\/script>",e=e.removeChild(e.firstChild)):typeof a.is=="string"?e=s.createElement(n,{is:a.is}):(e=s.createElement(n),n==="select"&&(s=e,a.multiple?s.multiple=!0:a.size&&(s.size=a.size))):e=s.createElementNS(e,n),e[He]=t,e[aa]=a,Sp(e,t,!1,!1),t.stateNode=e;e:{switch(s=$i(n,a),n){case"dialog":A("cancel",e),A("close",e),i=a;break;case"iframe":case"object":case"embed":A("load",e),i=a;break;case"video":case"audio":for(i=0;i<Dn.length;i++)A(Dn[i],e);i=a;break;case"source":A("error",e),i=a;break;case"img":case"image":case"link":A("error",e),A("load",e),i=a;break;case"details":A("toggle",e),i=a;break;case"input":Ws(e,a),i=Pi(e,a),A("invalid",e);break;case"option":i=a;break;case"select":e._wrapperState={wasMultiple:!!a.multiple},i=q({},a,{value:void 0}),A("invalid",e);break;case"textarea":Ys(e,a),i=Li(e,a),A("invalid",e);break;default:i=a}Ii(n,i),l=i;for(o in l)if(l.hasOwnProperty(o)){var c=l[o];o==="style"?rc(e,c):o==="dangerouslySetInnerHTML"?(c=c?c.__html:void 0,c!=null&&nc(e,c)):o==="children"?typeof c=="string"?(n!=="textarea"||c!=="")&&Qn(e,c):typeof c=="number"&&Qn(e,""+c):o!=="suppressContentEditableWarning"&&o!=="suppressHydrationWarning"&&o!=="autoFocus"&&(qn.hasOwnProperty(o)?c!=null&&o==="onScroll"&&A("scroll",e):c!=null&&bo(e,o,c,s))}switch(n){case"input":Ca(e),Hs(e,a,!1);break;case"textarea":Ca(e),qs(e);break;case"option":a.value!=null&&e.setAttribute("value",""+zt(a.value));break;case"select":e.multiple=!!a.multiple,o=a.value,o!=null?on(e,!!a.multiple,o,!1):a.defaultValue!=null&&on(e,!!a.multiple,a.defaultValue,!0);break;default:typeof i.onClick=="function"&&(e.onclick=lr)}switch(n){case"button":case"input":case"select":case"textarea":a=!!a.autoFocus;break e;case"img":a=!0;break e;default:a=!1}}a&&(t.flags|=4)}t.ref!==null&&(t.flags|=512,t.flags|=2097152)}return le(t),null;case 6:if(e&&t.stateNode!=null)Mp(e,t,e.memoizedProps,a);else{if(typeof a!="string"&&t.stateNode===null)throw Error(b(166));if(n=It(ia.current),It(qe.current),Aa(t)){if(a=t.stateNode,n=t.memoizedProps,a[He]=t,(o=a.nodeValue!==n)&&(e=ke,e!==null))switch(e.tag){case 3:Oa(a.nodeValue,n,(e.mode&1)!==0);break;case 5:e.memoizedProps.suppressHydrationWarning!==!0&&Oa(a.nodeValue,n,(e.mode&1)!==0)}o&&(t.flags|=4)}else a=(n.nodeType===9?n:n.ownerDocument).createTextNode(a),a[He]=t,t.stateNode=a}return le(t),null;case 13:if(F(H),a=t.memoizedState,e===null||e.memoizedState!==null&&e.memoizedState.dehydrated!==null){if(B&&we!==null&&t.mode&1&&!(t.flags&128))Hc(),mn(),t.flags|=98560,o=!1;else if(o=Aa(t),a!==null&&a.dehydrated!==null){if(e===null){if(!o)throw Error(b(318));if(o=t.memoizedState,o=o!==null?o.dehydrated:null,!o)throw Error(b(317));o[He]=t}else mn(),!(t.flags&128)&&(t.memoizedState=null),t.flags|=4;le(t),o=!1}else Oe!==null&&(vo(Oe),Oe=null),o=!0;if(!o)return t.flags&65536?t:null}return t.flags&128?(t.lanes=n,t):(a=a!==null,a!==(e!==null&&e.memoizedState!==null)&&a&&(t.child.flags|=8192,t.mode&1&&(e===null||H.current&1?J===0&&(J=3):ts())),t.updateQueue!==null&&(t.flags|=4),le(t),null);case 4:return vn(),so(e,t),e===null&&ta(t.stateNode.containerInfo),le(t),null;case 10:return Oo(t.type._context),le(t),null;case 17:return he(t.type)&&cr(),le(t),null;case 19:if(F(H),o=t.memoizedState,o===null)return le(t),null;if(a=(t.flags&128)!==0,s=o.rendering,s===null)if(a)Rn(o,!1);else{if(J!==0||e!==null&&e.flags&128)for(e=t.child;e!==null;){if(s=vr(e),s!==null){for(t.flags|=128,Rn(o,!1),a=s.updateQueue,a!==null&&(t.updateQueue=a,t.flags|=4),t.subtreeFlags=0,a=n,n=t.child;n!==null;)o=n,e=a,o.flags&=14680066,s=o.alternate,s===null?(o.childLanes=0,o.lanes=e,o.child=null,o.subtreeFlags=0,o.memoizedProps=null,o.memoizedState=null,o.updateQueue=null,o.dependencies=null,o.stateNode=null):(o.childLanes=s.childLanes,o.lanes=s.lanes,o.child=s.child,o.subtreeFlags=0,o.deletions=null,o.memoizedProps=s.memoizedProps,o.memoizedState=s.memoizedState,o.updateQueue=s.updateQueue,o.type=s.type,e=s.dependencies,o.dependencies=e===null?null:{lanes:e.lanes,firstContext:e.firstContext}),n=n.sibling;return D(H,H.current&1|2),t.child}e=e.sibling}o.tail!==null&&G()>hn&&(t.flags|=128,a=!0,Rn(o,!1),t.lanes=4194304)}else{if(!a)if(e=vr(s),e!==null){if(t.flags|=128,a=!0,n=e.updateQueue,n!==null&&(t.updateQueue=n,t.flags|=4),Rn(o,!0),o.tail===null&&o.tailMode==="hidden"&&!s.alternate&&!B)return le(t),null}else 2*G()-o.renderingStartTime>hn&&n!==1073741824&&(t.flags|=128,a=!0,Rn(o,!1),t.lanes=4194304);o.isBackwards?(s.sibling=t.child,t.child=s):(n=o.last,n!==null?n.sibling=s:t.child=s,o.last=s)}return o.tail!==null?(t=o.tail,o.rendering=t,o.tail=t.sibling,o.renderingStartTime=G(),t.sibling=null,n=H.current,D(H,a?n&1|2:n&1),t):(le(t),null);case 22:case 23:return es(),a=t.memoizedState!==null,e!==null&&e.memoizedState!==null!==a&&(t.flags|=8192),a&&t.mode&1?ye&1073741824&&(le(t),t.subtreeFlags&6&&(t.flags|=8192)):le(t),null;case 24:return null;case 25:return null}throw Error(b(156,t.tag))}function xf(e,t){switch(Io(t),t.tag){case 1:return he(t.type)&&cr(),e=t.flags,e&65536?(t.flags=e&-65537|128,t):null;case 3:return vn(),F(xe),F(pe),Vo(),e=t.flags,e&65536&&!(e&128)?(t.flags=e&-65537|128,t):null;case 5:return Uo(t),null;case 13:if(F(H),e=t.memoizedState,e!==null&&e.dehydrated!==null){if(t.alternate===null)throw Error(b(340));mn()}return e=t.flags,e&65536?(t.flags=e&-65537|128,t):null;case 19:return F(H),null;case 4:return vn(),null;case 10:return Oo(t.type._context),null;case 22:case 23:return es(),null;case 24:return null;default:return null}}var Ua=!1,ce=!1,hf=typeof WeakSet=="function"?WeakSet:Set,z=null;function an(e,t){var n=e.ref;if(n!==null)if(typeof n=="function")try{n(null)}catch(a){Q(e,t,a)}else n.current=null}function lo(e,t,n){try{n()}catch(a){Q(e,t,a)}}var Dl=!1;function bf(e,t){if(Hi=ir,e=Lc(),Lo(e)){if("selectionStart"in e)var n={start:e.selectionStart,end:e.selectionEnd};else e:{n=(n=e.ownerDocument)&&n.defaultView||window;var a=n.getSelection&&n.getSelection();if(a&&a.rangeCount!==0){n=a.anchorNode;var i=a.anchorOffset,o=a.focusNode;a=a.focusOffset;try{n.nodeType,o.nodeType}catch{n=null;break e}var s=0,l=-1,c=-1,p=0,f=0,g=e,v=null;t:for(;;){for(var y;g!==n||i!==0&&g.nodeType!==3||(l=s+i),g!==o||a!==0&&g.nodeType!==3||(c=s+a),g.nodeType===3&&(s+=g.nodeValue.length),(y=g.firstChild)!==null;)v=g,g=y;for(;;){if(g===e)break t;if(v===n&&++p===i&&(l=s),v===o&&++f===a&&(c=s),(y=g.nextSibling)!==null)break;g=v,v=g.parentNode}g=y}n=l===-1||c===-1?null:{start:l,end:c}}else n=null}n=n||{start:0,end:0}}else n=null;for(Yi={focusedElem:e,selectionRange:n},ir=!1,z=t;z!==null;)if(t=z,e=t.child,(t.subtreeFlags&1028)!==0&&e!==null)e.return=t,z=e;else for(;z!==null;){t=z;try{var w=t.alternate;if(t.flags&1024)switch(t.tag){case 0:case 11:case 15:break;case 1:if(w!==null){var k=w.memoizedProps,S=w.memoizedState,u=t.stateNode,d=u.getSnapshotBeforeUpdate(t.elementType===t.type?k:je(t.type,k),S);u.__reactInternalSnapshotBeforeUpdate=d}break;case 3:var m=t.stateNode.containerInfo;m.nodeType===1?m.textContent="":m.nodeType===9&&m.documentElement&&m.removeChild(m.documentElement);break;case 5:case 6:case 4:case 17:break;default:throw Error(b(163))}}catch(h){Q(t,t.return,h)}if(e=t.sibling,e!==null){e.return=t.return,z=e;break}z=t.return}return w=Dl,Dl=!1,w}function Wn(e,t,n){var a=t.updateQueue;if(a=a!==null?a.lastEffect:null,a!==null){var i=a=a.next;do{if((i.tag&e)===e){var o=i.destroy;i.destroy=void 0,o!==void 0&&lo(t,n,o)}i=i.next}while(i!==a)}}function Lr(e,t){if(t=t.updateQueue,t=t!==null?t.lastEffect:null,t!==null){var n=t=t.next;do{if((n.tag&e)===e){var a=n.create;n.destroy=a()}n=n.next}while(n!==t)}}function co(e){var t=e.ref;if(t!==null){var n=e.stateNode;switch(e.tag){case 5:e=n;break;default:e=n}typeof t=="function"?t(e):t.current=e}}function Ep(e){var t=e.alternate;t!==null&&(e.alternate=null,Ep(t)),e.child=null,e.deletions=null,e.sibling=null,e.tag===5&&(t=e.stateNode,t!==null&&(delete t[He],delete t[aa],delete t[Gi],delete t[ef],delete t[tf])),e.stateNode=null,e.return=null,e.dependencies=null,e.memoizedProps=null,e.memoizedState=null,e.pendingProps=null,e.stateNode=null,e.updateQueue=null}function Pp(e){return e.tag===5||e.tag===3||e.tag===4}function Ol(e){e:for(;;){for(;e.sibling===null;){if(e.return===null||Pp(e.return))return null;e=e.return}for(e.sibling.return=e.return,e=e.sibling;e.tag!==5&&e.tag!==6&&e.tag!==18;){if(e.flags&2||e.child===null||e.tag===4)continue e;e.child.return=e,e=e.child}if(!(e.flags&2))return e.stateNode}}function po(e,t,n){var a=e.tag;if(a===5||a===6)e=e.stateNode,t?n.nodeType===8?n.parentNode.insertBefore(e,t):n.insertBefore(e,t):(n.nodeType===8?(t=n.parentNode,t.insertBefore(e,n)):(t=n,t.appendChild(e)),n=n._reactRootContainer,n!=null||t.onclick!==null||(t.onclick=lr));else if(a!==4&&(e=e.child,e!==null))for(po(e,t,n),e=e.sibling;e!==null;)po(e,t,n),e=e.sibling}function uo(e,t,n){var a=e.tag;if(a===5||a===6)e=e.stateNode,t?n.insertBefore(e,t):n.appendChild(e);else if(a!==4&&(e=e.child,e!==null))for(uo(e,t,n),e=e.sibling;e!==null;)uo(e,t,n),e=e.sibling}var ae=null,De=!1;function ct(e,t,n){for(n=n.child;n!==null;)Tp(e,t,n),n=n.sibling}function Tp(e,t,n){if(Ye&&typeof Ye.onCommitFiberUnmount=="function")try{Ye.onCommitFiberUnmount(zr,n)}catch{}switch(n.tag){case 5:ce||an(n,t);case 6:var a=ae,i=De;ae=null,ct(e,t,n),ae=a,De=i,ae!==null&&(De?(e=ae,n=n.stateNode,e.nodeType===8?e.parentNode.removeChild(n):e.removeChild(n)):ae.removeChild(n.stateNode));break;case 18:ae!==null&&(De?(e=ae,n=n.stateNode,e.nodeType===8?mi(e.parentNode,n):e.nodeType===1&&mi(e,n),Jn(e)):mi(ae,n.stateNode));break;case 4:a=ae,i=De,ae=n.stateNode.containerInfo,De=!0,ct(e,t,n),ae=a,De=i;break;case 0:case 11:case 14:case 15:if(!ce&&(a=n.updateQueue,a!==null&&(a=a.lastEffect,a!==null))){i=a=a.next;do{var o=i,s=o.destroy;o=o.tag,s!==void 0&&(o&2||o&4)&&lo(n,t,s),i=i.next}while(i!==a)}ct(e,t,n);break;case 1:if(!ce&&(an(n,t),a=n.stateNode,typeof a.componentWillUnmount=="function"))try{a.props=n.memoizedProps,a.state=n.memoizedState,a.componentWillUnmount()}catch(l){Q(n,t,l)}ct(e,t,n);break;case 21:ct(e,t,n);break;case 22:n.mode&1?(ce=(a=ce)||n.memoizedState!==null,ct(e,t,n),ce=a):ct(e,t,n);break;default:ct(e,t,n)}}function Al(e){var t=e.updateQueue;if(t!==null){e.updateQueue=null;var n=e.stateNode;n===null&&(n=e.stateNode=new hf),t.forEach(function(a){var i=Ef.bind(null,e,a);n.has(a)||(n.add(a),a.then(i,i))})}}function $e(e,t){var n=t.deletions;if(n!==null)for(var a=0;a<n.length;a++){var i=n[a];try{var o=e,s=t,l=s;e:for(;l!==null;){switch(l.tag){case 5:ae=l.stateNode,De=!1;break e;case 3:ae=l.stateNode.containerInfo,De=!0;break e;case 4:ae=l.stateNode.containerInfo,De=!0;break e}l=l.return}if(ae===null)throw Error(b(160));Tp(o,s,i),ae=null,De=!1;var c=i.alternate;c!==null&&(c.return=null),i.return=null}catch(p){Q(i,t,p)}}if(t.subtreeFlags&12854)for(t=t.child;t!==null;)Rp(t,e),t=t.sibling}function Rp(e,t){var n=e.alternate,a=e.flags;switch(e.tag){case 0:case 11:case 14:case 15:if($e(t,e),Ve(e),a&4){try{Wn(3,e,e.return),Lr(3,e)}catch(k){Q(e,e.return,k)}try{Wn(5,e,e.return)}catch(k){Q(e,e.return,k)}}break;case 1:$e(t,e),Ve(e),a&512&&n!==null&&an(n,n.return);break;case 5:if($e(t,e),Ve(e),a&512&&n!==null&&an(n,n.return),e.flags&32){var i=e.stateNode;try{Qn(i,"")}catch(k){Q(e,e.return,k)}}if(a&4&&(i=e.stateNode,i!=null)){var o=e.memoizedProps,s=n!==null?n.memoizedProps:o,l=e.type,c=e.updateQueue;if(e.updateQueue=null,c!==null)try{l==="input"&&o.type==="radio"&&o.name!=null&&Zl(i,o),$i(l,s);var p=$i(l,o);for(s=0;s<c.length;s+=2){var f=c[s],g=c[s+1];f==="style"?rc(i,g):f==="dangerouslySetInnerHTML"?nc(i,g):f==="children"?Qn(i,g):bo(i,f,g,p)}switch(l){case"input":Ti(i,o);break;case"textarea":ec(i,o);break;case"select":var v=i._wrapperState.wasMultiple;i._wrapperState.wasMultiple=!!o.multiple;var y=o.value;y!=null?on(i,!!o.multiple,y,!1):v!==!!o.multiple&&(o.defaultValue!=null?on(i,!!o.multiple,o.defaultValue,!0):on(i,!!o.multiple,o.multiple?[]:"",!1))}i[aa]=o}catch(k){Q(e,e.return,k)}}break;case 6:if($e(t,e),Ve(e),a&4){if(e.stateNode===null)throw Error(b(162));i=e.stateNode,o=e.memoizedProps;try{i.nodeValue=o}catch(k){Q(e,e.return,k)}}break;case 3:if($e(t,e),Ve(e),a&4&&n!==null&&n.memoizedState.isDehydrated)try{Jn(t.containerInfo)}catch(k){Q(e,e.return,k)}break;case 4:$e(t,e),Ve(e);break;case 13:$e(t,e),Ve(e),i=e.child,i.flags&8192&&(o=i.memoizedState!==null,i.stateNode.isHidden=o,!o||i.alternate!==null&&i.alternate.memoizedState!==null||(Jo=G())),a&4&&Al(e);break;case 22:if(f=n!==null&&n.memoizedState!==null,e.mode&1?(ce=(p=ce)||f,$e(t,e),ce=p):$e(t,e),Ve(e),a&8192){if(p=e.memoizedState!==null,(e.stateNode.isHidden=p)&&!f&&e.mode&1)for(z=e,f=e.child;f!==null;){for(g=z=f;z!==null;){switch(v=z,y=v.child,v.tag){case 0:case 11:case 14:case 15:Wn(4,v,v.return);break;case 1:an(v,v.return);var w=v.stateNode;if(typeof w.componentWillUnmount=="function"){a=v,n=v.return;try{t=a,w.props=t.memoizedProps,w.state=t.memoizedState,w.componentWillUnmount()}catch(k){Q(a,n,k)}}break;case 5:an(v,v.return);break;case 22:if(v.memoizedState!==null){Bl(g);continue}}y!==null?(y.return=v,z=y):Bl(g)}f=f.sibling}e:for(f=null,g=e;;){if(g.tag===5){if(f===null){f=g;try{i=g.stateNode,p?(o=i.style,typeof o.setProperty=="function"?o.setProperty("display","none","important"):o.display="none"):(l=g.stateNode,c=g.memoizedProps.style,s=c!=null&&c.hasOwnProperty("display")?c.display:null,l.style.display=ac("display",s))}catch(k){Q(e,e.return,k)}}}else if(g.tag===6){if(f===null)try{g.stateNode.nodeValue=p?"":g.memoizedProps}catch(k){Q(e,e.return,k)}}else if((g.tag!==22&&g.tag!==23||g.memoizedState===null||g===e)&&g.child!==null){g.child.return=g,g=g.child;continue}if(g===e)break e;for(;g.sibling===null;){if(g.return===null||g.return===e)break e;f===g&&(f=null),g=g.return}f===g&&(f=null),g.sibling.return=g.return,g=g.sibling}}break;case 19:$e(t,e),Ve(e),a&4&&Al(e);break;case 21:break;default:$e(t,e),Ve(e)}}function Ve(e){var t=e.flags;if(t&2){try{e:{for(var n=e.return;n!==null;){if(Pp(n)){var a=n;break e}n=n.return}throw Error(b(160))}switch(a.tag){case 5:var i=a.stateNode;a.flags&32&&(Qn(i,""),a.flags&=-33);var o=Ol(e);uo(e,o,i);break;case 3:case 4:var s=a.stateNode.containerInfo,l=Ol(e);po(e,l,s);break;default:throw Error(b(161))}}catch(c){Q(e,e.return,c)}e.flags&=-3}t&4096&&(e.flags&=-4097)}function yf(e,t,n){z=e,Lp(e,t,n)}function Lp(e,t,n){for(var a=(e.mode&1)!==0;z!==null;){var i=z,o=i.child;if(i.tag===22&&a){var s=i.memoizedState!==null||Ua;if(!s){var l=i.alternate,c=l!==null&&l.memoizedState!==null||ce;l=Ua;var p=ce;if(Ua=s,(ce=c)&&!p)for(z=i;z!==null;)s=z,c=s.child,s.tag===22&&s.memoizedState!==null?Ul(i):c!==null?(c.return=s,z=c):Ul(i);for(;o!==null;)z=o,Lp(o,t,n),o=o.sibling;z=i,Ua=l,ce=p}Fl(e,t,n)}else i.subtreeFlags&8772&&o!==null?(o.return=i,z=o):Fl(e,t,n)}}function Fl(e){for(;z!==null;){var t=z;if(t.flags&8772){var n=t.alternate;try{if(t.flags&8772)switch(t.tag){case 0:case 11:case 15:ce||Lr(5,t);break;case 1:var a=t.stateNode;if(t.flags&4&&!ce)if(n===null)a.componentDidMount();else{var i=t.elementType===t.type?n.memoizedProps:je(t.type,n.memoizedProps);a.componentDidUpdate(i,n.memoizedState,a.__reactInternalSnapshotBeforeUpdate)}var o=t.updateQueue;o!==null&&zl(t,o,a);break;case 3:var s=t.updateQueue;if(s!==null){if(n=null,t.child!==null)switch(t.child.tag){case 5:n=t.child.stateNode;break;case 1:n=t.child.stateNode}zl(t,s,n)}break;case 5:var l=t.stateNode;if(n===null&&t.flags&4){n=l;var c=t.memoizedProps;switch(t.type){case"button":case"input":case"select":case"textarea":c.autoFocus&&n.focus();break;case"img":c.src&&(n.src=c.src)}}break;case 6:break;case 4:break;case 12:break;case 13:if(t.memoizedState===null){var p=t.alternate;if(p!==null){var f=p.memoizedState;if(f!==null){var g=f.dehydrated;g!==null&&Jn(g)}}}break;case 19:case 17:case 21:case 22:case 23:case 25:break;default:throw Error(b(163))}ce||t.flags&512&&co(t)}catch(v){Q(t,t.return,v)}}if(t===e){z=null;break}if(n=t.sibling,n!==null){n.return=t.return,z=n;break}z=t.return}}function Bl(e){for(;z!==null;){var t=z;if(t===e){z=null;break}var n=t.sibling;if(n!==null){n.return=t.return,z=n;break}z=t.return}}function Ul(e){for(;z!==null;){var t=z;try{switch(t.tag){case 0:case 11:case 15:var n=t.return;try{Lr(4,t)}catch(c){Q(t,n,c)}break;case 1:var a=t.stateNode;if(typeof a.componentDidMount=="function"){var i=t.return;try{a.componentDidMount()}catch(c){Q(t,i,c)}}var o=t.return;try{co(t)}catch(c){Q(t,o,c)}break;case 5:var s=t.return;try{co(t)}catch(c){Q(t,s,c)}}}catch(c){Q(t,t.return,c)}if(t===e){z=null;break}var l=t.sibling;if(l!==null){l.return=t.return,z=l;break}z=t.return}}var wf=Math.ceil,br=it.ReactCurrentDispatcher,Xo=it.ReactCurrentOwner,Te=it.ReactCurrentBatchConfig,T=0,te=null,X=null,re=0,ye=0,rn=Mt(0),J=0,ca=null,Ft=0,_r=0,Ko=0,Hn=null,ge=null,Jo=0,hn=1/0,Xe=null,yr=!1,fo=null,wt=null,Va=!1,gt=null,wr=0,Yn=0,mo=null,Ka=-1,Ja=0;function fe(){return T&6?G():Ka!==-1?Ka:Ka=G()}function kt(e){return e.mode&1?T&2&&re!==0?re&-re:af.transition!==null?(Ja===0&&(Ja=vc()),Ja):(e=_,e!==0||(e=window.event,e=e===void 0?16:Nc(e.type)),e):1}function Fe(e,t,n,a){if(50<Yn)throw Yn=0,mo=null,Error(b(185));pa(e,n,a),(!(T&2)||e!==te)&&(e===te&&(!(T&2)&&(_r|=n),J===4&&ft(e,re)),be(e,a),n===1&&T===0&&!(t.mode&1)&&(hn=G()+500,Pr&&Et()))}function be(e,t){var n=e.callbackNode;iu(e,t);var a=rr(e,e===te?re:0);if(a===0)n!==null&&Xs(n),e.callbackNode=null,e.callbackPriority=0;else if(t=a&-a,e.callbackPriority!==t){if(n!=null&&Xs(n),t===1)e.tag===0?nf(Vl.bind(null,e)):Uc(Vl.bind(null,e)),Ju(function(){!(T&6)&&Et()}),n=null;else{switch(xc(a)){case 1:n=zo;break;case 4:n=mc;break;case 16:n=ar;break;case 536870912:n=gc;break;default:n=ar}n=Fp(n,_p.bind(null,e))}e.callbackPriority=t,e.callbackNode=n}}function _p(e,t){if(Ka=-1,Ja=0,T&6)throw Error(b(327));var n=e.callbackNode;if(dn()&&e.callbackNode!==n)return null;var a=rr(e,e===te?re:0);if(a===0)return null;if(a&30||a&e.expiredLanes||t)t=kr(e,a);else{t=a;var i=T;T|=2;var o=$p();(te!==e||re!==t)&&(Xe=null,hn=G()+500,$t(e,t));do try{zf();break}catch(l){Ip(e,l)}while(!0);Do(),br.current=o,T=i,X!==null?t=0:(te=null,re=0,t=J)}if(t!==0){if(t===2&&(i=Fi(e),i!==0&&(a=i,t=go(e,i))),t===1)throw n=ca,$t(e,0),ft(e,a),be(e,G()),n;if(t===6)ft(e,a);else{if(i=e.current.alternate,!(a&30)&&!kf(i)&&(t=kr(e,a),t===2&&(o=Fi(e),o!==0&&(a=o,t=go(e,o))),t===1))throw n=ca,$t(e,0),ft(e,a),be(e,G()),n;switch(e.finishedWork=i,e.finishedLanes=a,t){case 0:case 1:throw Error(b(345));case 2:Rt(e,ge,Xe);break;case 3:if(ft(e,a),(a&130023424)===a&&(t=Jo+500-G(),10<t)){if(rr(e,0)!==0)break;if(i=e.suspendedLanes,(i&a)!==a){fe(),e.pingedLanes|=e.suspendedLanes&i;break}e.timeoutHandle=Qi(Rt.bind(null,e,ge,Xe),t);break}Rt(e,ge,Xe);break;case 4:if(ft(e,a),(a&4194240)===a)break;for(t=e.eventTimes,i=-1;0<a;){var s=31-Ae(a);o=1<<s,s=t[s],s>i&&(i=s),a&=~o}if(a=i,a=G()-a,a=(120>a?120:480>a?480:1080>a?1080:1920>a?1920:3e3>a?3e3:4320>a?4320:1960*wf(a/1960))-a,10<a){e.timeoutHandle=Qi(Rt.bind(null,e,ge,Xe),a);break}Rt(e,ge,Xe);break;case 5:Rt(e,ge,Xe);break;default:throw Error(b(329))}}}return be(e,G()),e.callbackNode===n?_p.bind(null,e):null}function go(e,t){var n=Hn;return e.current.memoizedState.isDehydrated&&($t(e,t).flags|=256),e=kr(e,t),e!==2&&(t=ge,ge=n,t!==null&&vo(t)),e}function vo(e){ge===null?ge=e:ge.push.apply(ge,e)}function kf(e){for(var t=e;;){if(t.flags&16384){var n=t.updateQueue;if(n!==null&&(n=n.stores,n!==null))for(var a=0;a<n.length;a++){var i=n[a],o=i.getSnapshot;i=i.value;try{if(!Be(o(),i))return!1}catch{return!1}}}if(n=t.child,t.subtreeFlags&16384&&n!==null)n.return=t,t=n;else{if(t===e)break;for(;t.sibling===null;){if(t.return===null||t.return===e)return!0;t=t.return}t.sibling.return=t.return,t=t.sibling}}return!0}function ft(e,t){for(t&=~Ko,t&=~_r,e.suspendedLanes|=t,e.pingedLanes&=~t,e=e.expirationTimes;0<t;){var n=31-Ae(t),a=1<<n;e[n]=-1,t&=~a}}function Vl(e){if(T&6)throw Error(b(327));dn();var t=rr(e,0);if(!(t&1))return be(e,G()),null;var n=kr(e,t);if(e.tag!==0&&n===2){var a=Fi(e);a!==0&&(t=a,n=go(e,a))}if(n===1)throw n=ca,$t(e,0),ft(e,t),be(e,G()),n;if(n===6)throw Error(b(345));return e.finishedWork=e.current.alternate,e.finishedLanes=t,Rt(e,ge,Xe),be(e,G()),null}function Zo(e,t){var n=T;T|=1;try{return e(t)}finally{T=n,T===0&&(hn=G()+500,Pr&&Et())}}function Bt(e){gt!==null&&gt.tag===0&&!(T&6)&&dn();var t=T;T|=1;var n=Te.transition,a=_;try{if(Te.transition=null,_=1,e)return e()}finally{_=a,Te.transition=n,T=t,!(T&6)&&Et()}}function es(){ye=rn.current,F(rn)}function $t(e,t){e.finishedWork=null,e.finishedLanes=0;var n=e.timeoutHandle;if(n!==-1&&(e.timeoutHandle=-1,Ku(n)),X!==null)for(n=X.return;n!==null;){var a=n;switch(Io(a),a.tag){case 1:a=a.type.childContextTypes,a!=null&&cr();break;case 3:vn(),F(xe),F(pe),Vo();break;case 5:Uo(a);break;case 4:vn();break;case 13:F(H);break;case 19:F(H);break;case 10:Oo(a.type._context);break;case 22:case 23:es()}n=n.return}if(te=e,X=e=Nt(e.current,null),re=ye=t,J=0,ca=null,Ko=_r=Ft=0,ge=Hn=null,_t!==null){for(t=0;t<_t.length;t++)if(n=_t[t],a=n.interleaved,a!==null){n.interleaved=null;var i=a.next,o=n.pending;if(o!==null){var s=o.next;o.next=i,a.next=s}n.pending=a}_t=null}return e}function Ip(e,t){do{var n=X;try{if(Do(),Qa.current=hr,xr){for(var a=Y.memoizedState;a!==null;){var i=a.queue;i!==null&&(i.pending=null),a=a.next}xr=!1}if(At=0,ee=K=Y=null,Vn=!1,oa=0,Xo.current=null,n===null||n.return===null){J=1,ca=t,X=null;break}e:{var o=e,s=n.return,l=n,c=t;if(t=re,l.flags|=32768,c!==null&&typeof c=="object"&&typeof c.then=="function"){var p=c,f=l,g=f.tag;if(!(f.mode&1)&&(g===0||g===11||g===15)){var v=f.alternate;v?(f.updateQueue=v.updateQueue,f.memoizedState=v.memoizedState,f.lanes=v.lanes):(f.updateQueue=null,f.memoizedState=null)}var y=Tl(s);if(y!==null){y.flags&=-257,Rl(y,s,l,o,t),y.mode&1&&Pl(o,p,t),t=y,c=p;var w=t.updateQueue;if(w===null){var k=new Set;k.add(c),t.updateQueue=k}else w.add(c);break e}else{if(!(t&1)){Pl(o,p,t),ts();break e}c=Error(b(426))}}else if(B&&l.mode&1){var S=Tl(s);if(S!==null){!(S.flags&65536)&&(S.flags|=256),Rl(S,s,l,o,t),$o(xn(c,l));break e}}o=c=xn(c,l),J!==4&&(J=2),Hn===null?Hn=[o]:Hn.push(o),o=s;do{switch(o.tag){case 3:o.flags|=65536,t&=-t,o.lanes|=t;var u=xp(o,c,t);Nl(o,u);break e;case 1:l=c;var d=o.type,m=o.stateNode;if(!(o.flags&128)&&(typeof d.getDerivedStateFromError=="function"||m!==null&&typeof m.componentDidCatch=="function"&&(wt===null||!wt.has(m)))){o.flags|=65536,t&=-t,o.lanes|=t;var h=hp(o,l,t);Nl(o,h);break e}}o=o.return}while(o!==null)}Dp(n)}catch(N){t=N,X===n&&n!==null&&(X=n=n.return);continue}break}while(!0)}function $p(){var e=br.current;return br.current=hr,e===null?hr:e}function ts(){(J===0||J===3||J===2)&&(J=4),te===null||!(Ft&268435455)&&!(_r&268435455)||ft(te,re)}function kr(e,t){var n=T;T|=2;var a=$p();(te!==e||re!==t)&&(Xe=null,$t(e,t));do try{Nf();break}catch(i){Ip(e,i)}while(!0);if(Do(),T=n,br.current=a,X!==null)throw Error(b(261));return te=null,re=0,J}function Nf(){for(;X!==null;)jp(X)}function zf(){for(;X!==null&&!Xd();)jp(X)}function jp(e){var t=Ap(e.alternate,e,ye);e.memoizedProps=e.pendingProps,t===null?Dp(e):X=t,Xo.current=null}function Dp(e){var t=e;do{var n=t.alternate;if(e=t.return,t.flags&32768){if(n=xf(n,t),n!==null){n.flags&=32767,X=n;return}if(e!==null)e.flags|=32768,e.subtreeFlags=0,e.deletions=null;else{J=6,X=null;return}}else if(n=vf(n,t,ye),n!==null){X=n;return}if(t=t.sibling,t!==null){X=t;return}X=t=e}while(t!==null);J===0&&(J=5)}function Rt(e,t,n){var a=_,i=Te.transition;try{Te.transition=null,_=1,Sf(e,t,n,a)}finally{Te.transition=i,_=a}return null}function Sf(e,t,n,a){do dn();while(gt!==null);if(T&6)throw Error(b(327));n=e.finishedWork;var i=e.finishedLanes;if(n===null)return null;if(e.finishedWork=null,e.finishedLanes=0,n===e.current)throw Error(b(177));e.callbackNode=null,e.callbackPriority=0;var o=n.lanes|n.childLanes;if(ou(e,o),e===te&&(X=te=null,re=0),!(n.subtreeFlags&2064)&&!(n.flags&2064)||Va||(Va=!0,Fp(ar,function(){return dn(),null})),o=(n.flags&15990)!==0,n.subtreeFlags&15990||o){o=Te.transition,Te.transition=null;var s=_;_=1;var l=T;T|=4,Xo.current=null,bf(e,n),Rp(n,e),Yu(Yi),ir=!!Hi,Yi=Hi=null,e.current=n,yf(n,e,i),Kd(),T=l,_=s,Te.transition=o}else e.current=n;if(Va&&(Va=!1,gt=e,wr=i),o=e.pendingLanes,o===0&&(wt=null),eu(n.stateNode,a),be(e,G()),t!==null)for(a=e.onRecoverableError,n=0;n<t.length;n++)i=t[n],a(i.value,{componentStack:i.stack,digest:i.digest});if(yr)throw yr=!1,e=fo,fo=null,e;return wr&1&&e.tag!==0&&dn(),o=e.pendingLanes,o&1?e===mo?Yn++:(Yn=0,mo=e):Yn=0,Et(),null}function dn(){if(gt!==null){var e=xc(wr),t=Te.transition,n=_;try{if(Te.transition=null,_=16>e?16:e,gt===null)var a=!1;else{if(e=gt,gt=null,wr=0,T&6)throw Error(b(331));var i=T;for(T|=4,z=e.current;z!==null;){var o=z,s=o.child;if(z.flags&16){var l=o.deletions;if(l!==null){for(var c=0;c<l.length;c++){var p=l[c];for(z=p;z!==null;){var f=z;switch(f.tag){case 0:case 11:case 15:Wn(8,f,o)}var g=f.child;if(g!==null)g.return=f,z=g;else for(;z!==null;){f=z;var v=f.sibling,y=f.return;if(Ep(f),f===p){z=null;break}if(v!==null){v.return=y,z=v;break}z=y}}}var w=o.alternate;if(w!==null){var k=w.child;if(k!==null){w.child=null;do{var S=k.sibling;k.sibling=null,k=S}while(k!==null)}}z=o}}if(o.subtreeFlags&2064&&s!==null)s.return=o,z=s;else e:for(;z!==null;){if(o=z,o.flags&2048)switch(o.tag){case 0:case 11:case 15:Wn(9,o,o.return)}var u=o.sibling;if(u!==null){u.return=o.return,z=u;break e}z=o.return}}var d=e.current;for(z=d;z!==null;){s=z;var m=s.child;if(s.subtreeFlags&2064&&m!==null)m.return=s,z=m;else e:for(s=d;z!==null;){if(l=z,l.flags&2048)try{switch(l.tag){case 0:case 11:case 15:Lr(9,l)}}catch(N){Q(l,l.return,N)}if(l===s){z=null;break e}var h=l.sibling;if(h!==null){h.return=l.return,z=h;break e}z=l.return}}if(T=i,Et(),Ye&&typeof Ye.onPostCommitFiberRoot=="function")try{Ye.onPostCommitFiberRoot(zr,e)}catch{}a=!0}return a}finally{_=n,Te.transition=t}}return!1}function Wl(e,t,n){t=xn(n,t),t=xp(e,t,1),e=yt(e,t,1),t=fe(),e!==null&&(pa(e,1,t),be(e,t))}function Q(e,t,n){if(e.tag===3)Wl(e,e,n);else for(;t!==null;){if(t.tag===3){Wl(t,e,n);break}else if(t.tag===1){var a=t.stateNode;if(typeof t.type.getDerivedStateFromError=="function"||typeof a.componentDidCatch=="function"&&(wt===null||!wt.has(a))){e=xn(n,e),e=hp(t,e,1),t=yt(t,e,1),e=fe(),t!==null&&(pa(t,1,e),be(t,e));break}}t=t.return}}function Cf(e,t,n){var a=e.pingCache;a!==null&&a.delete(t),t=fe(),e.pingedLanes|=e.suspendedLanes&n,te===e&&(re&n)===n&&(J===4||J===3&&(re&130023424)===re&&500>G()-Jo?$t(e,0):Ko|=n),be(e,t)}function Op(e,t){t===0&&(e.mode&1?(t=Pa,Pa<<=1,!(Pa&130023424)&&(Pa=4194304)):t=1);var n=fe();e=at(e,t),e!==null&&(pa(e,t,n),be(e,n))}function Mf(e){var t=e.memoizedState,n=0;t!==null&&(n=t.retryLane),Op(e,n)}function Ef(e,t){var n=0;switch(e.tag){case 13:var a=e.stateNode,i=e.memoizedState;i!==null&&(n=i.retryLane);break;case 19:a=e.stateNode;break;default:throw Error(b(314))}a!==null&&a.delete(t),Op(e,n)}var Ap;Ap=function(e,t,n){if(e!==null)if(e.memoizedProps!==t.pendingProps||xe.current)ve=!0;else{if(!(e.lanes&n)&&!(t.flags&128))return ve=!1,gf(e,t,n);ve=!!(e.flags&131072)}else ve=!1,B&&t.flags&1048576&&Vc(t,ur,t.index);switch(t.lanes=0,t.tag){case 2:var a=t.type;Xa(e,t),e=t.pendingProps;var i=fn(t,pe.current);pn(t,n),i=Ho(null,t,a,e,i,n);var o=Yo();return t.flags|=1,typeof i=="object"&&i!==null&&typeof i.render=="function"&&i.$$typeof===void 0?(t.tag=1,t.memoizedState=null,t.updateQueue=null,he(a)?(o=!0,pr(t)):o=!1,t.memoizedState=i.state!==null&&i.state!==void 0?i.state:null,Fo(t),i.updater=Rr,t.stateNode=i,i._reactInternals=t,to(t,a,e,n),t=ro(null,t,a,!0,o,n)):(t.tag=0,B&&o&&_o(t),ue(null,t,i,n),t=t.child),t;case 16:a=t.elementType;e:{switch(Xa(e,t),e=t.pendingProps,i=a._init,a=i(a._payload),t.type=a,i=t.tag=Tf(a),e=je(a,e),i){case 0:t=ao(null,t,a,e,n);break e;case 1:t=Il(null,t,a,e,n);break e;case 11:t=Ll(null,t,a,e,n);break e;case 14:t=_l(null,t,a,je(a.type,e),n);break e}throw Error(b(306,a,""))}return t;case 0:return a=t.type,i=t.pendingProps,i=t.elementType===a?i:je(a,i),ao(e,t,a,i,n);case 1:return a=t.type,i=t.pendingProps,i=t.elementType===a?i:je(a,i),Il(e,t,a,i,n);case 3:e:{if(kp(t),e===null)throw Error(b(387));a=t.pendingProps,o=t.memoizedState,i=o.element,Gc(e,t),gr(t,a,null,n);var s=t.memoizedState;if(a=s.element,o.isDehydrated)if(o={element:a,isDehydrated:!1,cache:s.cache,pendingSuspenseBoundaries:s.pendingSuspenseBoundaries,transitions:s.transitions},t.updateQueue.baseState=o,t.memoizedState=o,t.flags&256){i=xn(Error(b(423)),t),t=$l(e,t,a,n,i);break e}else if(a!==i){i=xn(Error(b(424)),t),t=$l(e,t,a,n,i);break e}else for(we=bt(t.stateNode.containerInfo.firstChild),ke=t,B=!0,Oe=null,n=qc(t,null,a,n),t.child=n;n;)n.flags=n.flags&-3|4096,n=n.sibling;else{if(mn(),a===i){t=rt(e,t,n);break e}ue(e,t,a,n)}t=t.child}return t;case 5:return Xc(t),e===null&&Ji(t),a=t.type,i=t.pendingProps,o=e!==null?e.memoizedProps:null,s=i.children,qi(a,i)?s=null:o!==null&&qi(a,o)&&(t.flags|=32),wp(e,t),ue(e,t,s,n),t.child;case 6:return e===null&&Ji(t),null;case 13:return Np(e,t,n);case 4:return Bo(t,t.stateNode.containerInfo),a=t.pendingProps,e===null?t.child=gn(t,null,a,n):ue(e,t,a,n),t.child;case 11:return a=t.type,i=t.pendingProps,i=t.elementType===a?i:je(a,i),Ll(e,t,a,i,n);case 7:return ue(e,t,t.pendingProps,n),t.child;case 8:return ue(e,t,t.pendingProps.children,n),t.child;case 12:return ue(e,t,t.pendingProps.children,n),t.child;case 10:e:{if(a=t.type._context,i=t.pendingProps,o=t.memoizedProps,s=i.value,D(fr,a._currentValue),a._currentValue=s,o!==null)if(Be(o.value,s)){if(o.children===i.children&&!xe.current){t=rt(e,t,n);break e}}else for(o=t.child,o!==null&&(o.return=t);o!==null;){var l=o.dependencies;if(l!==null){s=o.child;for(var c=l.firstContext;c!==null;){if(c.context===a){if(o.tag===1){c=et(-1,n&-n),c.tag=2;var p=o.updateQueue;if(p!==null){p=p.shared;var f=p.pending;f===null?c.next=c:(c.next=f.next,f.next=c),p.pending=c}}o.lanes|=n,c=o.alternate,c!==null&&(c.lanes|=n),Zi(o.return,n,t),l.lanes|=n;break}c=c.next}}else if(o.tag===10)s=o.type===t.type?null:o.child;else if(o.tag===18){if(s=o.return,s===null)throw Error(b(341));s.lanes|=n,l=s.alternate,l!==null&&(l.lanes|=n),Zi(s,n,t),s=o.sibling}else s=o.child;if(s!==null)s.return=o;else for(s=o;s!==null;){if(s===t){s=null;break}if(o=s.sibling,o!==null){o.return=s.return,s=o;break}s=s.return}o=s}ue(e,t,i.children,n),t=t.child}return t;case 9:return i=t.type,a=t.pendingProps.children,pn(t,n),i=Re(i),a=a(i),t.flags|=1,ue(e,t,a,n),t.child;case 14:return a=t.type,i=je(a,t.pendingProps),i=je(a.type,i),_l(e,t,a,i,n);case 15:return bp(e,t,t.type,t.pendingProps,n);case 17:return a=t.type,i=t.pendingProps,i=t.elementType===a?i:je(a,i),Xa(e,t),t.tag=1,he(a)?(e=!0,pr(t)):e=!1,pn(t,n),vp(t,a,i),to(t,a,i,n),ro(null,t,a,!0,e,n);case 19:return zp(e,t,n);case 22:return yp(e,t,n)}throw Error(b(156,t.tag))};function Fp(e,t){return fc(e,t)}function Pf(e,t,n,a){this.tag=e,this.key=n,this.sibling=this.child=this.return=this.stateNode=this.type=this.elementType=null,this.index=0,this.ref=null,this.pendingProps=t,this.dependencies=this.memoizedState=this.updateQueue=this.memoizedProps=null,this.mode=a,this.subtreeFlags=this.flags=0,this.deletions=null,this.childLanes=this.lanes=0,this.alternate=null}function Pe(e,t,n,a){return new Pf(e,t,n,a)}function ns(e){return e=e.prototype,!(!e||!e.isReactComponent)}function Tf(e){if(typeof e=="function")return ns(e)?1:0;if(e!=null){if(e=e.$$typeof,e===wo)return 11;if(e===ko)return 14}return 2}function Nt(e,t){var n=e.alternate;return n===null?(n=Pe(e.tag,t,e.key,e.mode),n.elementType=e.elementType,n.type=e.type,n.stateNode=e.stateNode,n.alternate=e,e.alternate=n):(n.pendingProps=t,n.type=e.type,n.flags=0,n.subtreeFlags=0,n.deletions=null),n.flags=e.flags&14680064,n.childLanes=e.childLanes,n.lanes=e.lanes,n.child=e.child,n.memoizedProps=e.memoizedProps,n.memoizedState=e.memoizedState,n.updateQueue=e.updateQueue,t=e.dependencies,n.dependencies=t===null?null:{lanes:t.lanes,firstContext:t.firstContext},n.sibling=e.sibling,n.index=e.index,n.ref=e.ref,n}function Za(e,t,n,a,i,o){var s=2;if(a=e,typeof e=="function")ns(e)&&(s=1);else if(typeof e=="string")s=5;else e:switch(e){case Qt:return jt(n.children,i,o,t);case yo:s=8,i|=8;break;case Si:return e=Pe(12,n,t,i|2),e.elementType=Si,e.lanes=o,e;case Ci:return e=Pe(13,n,t,i),e.elementType=Ci,e.lanes=o,e;case Mi:return e=Pe(19,n,t,i),e.elementType=Mi,e.lanes=o,e;case Xl:return Ir(n,i,o,t);default:if(typeof e=="object"&&e!==null)switch(e.$$typeof){case Ql:s=10;break e;case Gl:s=9;break e;case wo:s=11;break e;case ko:s=14;break e;case pt:s=16,a=null;break e}throw Error(b(130,e==null?e:typeof e,""))}return t=Pe(s,n,t,i),t.elementType=e,t.type=a,t.lanes=o,t}function jt(e,t,n,a){return e=Pe(7,e,a,t),e.lanes=n,e}function Ir(e,t,n,a){return e=Pe(22,e,a,t),e.elementType=Xl,e.lanes=n,e.stateNode={isHidden:!1},e}function ki(e,t,n){return e=Pe(6,e,null,t),e.lanes=n,e}function Ni(e,t,n){return t=Pe(4,e.children!==null?e.children:[],e.key,t),t.lanes=n,t.stateNode={containerInfo:e.containerInfo,pendingChildren:null,implementation:e.implementation},t}function Rf(e,t,n,a,i){this.tag=t,this.containerInfo=e,this.finishedWork=this.pingCache=this.current=this.pendingChildren=null,this.timeoutHandle=-1,this.callbackNode=this.pendingContext=this.context=null,this.callbackPriority=0,this.eventTimes=oi(0),this.expirationTimes=oi(-1),this.entangledLanes=this.finishedLanes=this.mutableReadLanes=this.expiredLanes=this.pingedLanes=this.suspendedLanes=this.pendingLanes=0,this.entanglements=oi(0),this.identifierPrefix=a,this.onRecoverableError=i,this.mutableSourceEagerHydrationData=null}function as(e,t,n,a,i,o,s,l,c){return e=new Rf(e,t,n,l,c),t===1?(t=1,o===!0&&(t|=8)):t=0,o=Pe(3,null,null,t),e.current=o,o.stateNode=e,o.memoizedState={element:a,isDehydrated:n,cache:null,transitions:null,pendingSuspenseBoundaries:null},Fo(o),e}function Lf(e,t,n){var a=3<arguments.length&&arguments[3]!==void 0?arguments[3]:null;return{$$typeof:qt,key:a==null?null:""+a,children:e,containerInfo:t,implementation:n}}function Bp(e){if(!e)return St;e=e._reactInternals;e:{if(Vt(e)!==e||e.tag!==1)throw Error(b(170));var t=e;do{switch(t.tag){case 3:t=t.stateNode.context;break e;case 1:if(he(t.type)){t=t.stateNode.__reactInternalMemoizedMergedChildContext;break e}}t=t.return}while(t!==null);throw Error(b(171))}if(e.tag===1){var n=e.type;if(he(n))return Bc(e,n,t)}return t}function Up(e,t,n,a,i,o,s,l,c){return e=as(n,a,!0,e,i,o,s,l,c),e.context=Bp(null),n=e.current,a=fe(),i=kt(n),o=et(a,i),o.callback=t??null,yt(n,o,i),e.current.lanes=i,pa(e,i,a),be(e,a),e}function $r(e,t,n,a){var i=t.current,o=fe(),s=kt(i);return n=Bp(n),t.context===null?t.context=n:t.pendingContext=n,t=et(o,s),t.payload={element:e},a=a===void 0?null:a,a!==null&&(t.callback=a),e=yt(i,t,s),e!==null&&(Fe(e,i,s,o),qa(e,i,s)),s}function Nr(e){if(e=e.current,!e.child)return null;switch(e.child.tag){case 5:return e.child.stateNode;default:return e.child.stateNode}}function Hl(e,t){if(e=e.memoizedState,e!==null&&e.dehydrated!==null){var n=e.retryLane;e.retryLane=n!==0&&n<t?n:t}}function rs(e,t){Hl(e,t),(e=e.alternate)&&Hl(e,t)}function _f(){return null}var Vp=typeof reportError=="function"?reportError:function(e){console.error(e)};function is(e){this._internalRoot=e}jr.prototype.render=is.prototype.render=function(e){var t=this._internalRoot;if(t===null)throw Error(b(409));$r(e,t,null,null)};jr.prototype.unmount=is.prototype.unmount=function(){var e=this._internalRoot;if(e!==null){this._internalRoot=null;var t=e.containerInfo;Bt(function(){$r(null,e,null,null)}),t[nt]=null}};function jr(e){this._internalRoot=e}jr.prototype.unstable_scheduleHydration=function(e){if(e){var t=yc();e={blockedOn:null,target:e,priority:t};for(var n=0;n<ut.length&&t!==0&&t<ut[n].priority;n++);ut.splice(n,0,e),n===0&&kc(e)}};function os(e){return!(!e||e.nodeType!==1&&e.nodeType!==9&&e.nodeType!==11)}function Dr(e){return!(!e||e.nodeType!==1&&e.nodeType!==9&&e.nodeType!==11&&(e.nodeType!==8||e.nodeValue!==" react-mount-point-unstable "))}function Yl(){}function If(e,t,n,a,i){if(i){if(typeof a=="function"){var o=a;a=function(){var p=Nr(s);o.call(p)}}var s=Up(t,a,e,0,null,!1,!1,"",Yl);return e._reactRootContainer=s,e[nt]=s.current,ta(e.nodeType===8?e.parentNode:e),Bt(),s}for(;i=e.lastChild;)e.removeChild(i);if(typeof a=="function"){var l=a;a=function(){var p=Nr(c);l.call(p)}}var c=as(e,0,!1,null,null,!1,!1,"",Yl);return e._reactRootContainer=c,e[nt]=c.current,ta(e.nodeType===8?e.parentNode:e),Bt(function(){$r(t,c,n,a)}),c}function Or(e,t,n,a,i){var o=n._reactRootContainer;if(o){var s=o;if(typeof i=="function"){var l=i;i=function(){var c=Nr(s);l.call(c)}}$r(t,s,e,i)}else s=If(n,t,e,i,a);return Nr(s)}hc=function(e){switch(e.tag){case 3:var t=e.stateNode;if(t.current.memoizedState.isDehydrated){var n=jn(t.pendingLanes);n!==0&&(So(t,n|1),be(t,G()),!(T&6)&&(hn=G()+500,Et()))}break;case 13:Bt(function(){var a=at(e,1);if(a!==null){var i=fe();Fe(a,e,1,i)}}),rs(e,1)}};Co=function(e){if(e.tag===13){var t=at(e,134217728);if(t!==null){var n=fe();Fe(t,e,134217728,n)}rs(e,134217728)}};bc=function(e){if(e.tag===13){var t=kt(e),n=at(e,t);if(n!==null){var a=fe();Fe(n,e,t,a)}rs(e,t)}};yc=function(){return _};wc=function(e,t){var n=_;try{return _=e,t()}finally{_=n}};Di=function(e,t,n){switch(t){case"input":if(Ti(e,n),t=n.name,n.type==="radio"&&t!=null){for(n=e;n.parentNode;)n=n.parentNode;for(n=n.querySelectorAll("input[name="+JSON.stringify(""+t)+'][type="radio"]'),t=0;t<n.length;t++){var a=n[t];if(a!==e&&a.form===e.form){var i=Er(a);if(!i)throw Error(b(90));Jl(a),Ti(a,i)}}}break;case"textarea":ec(e,n);break;case"select":t=n.value,t!=null&&on(e,!!n.multiple,t,!1)}};sc=Zo;lc=Bt;var $f={usingClientEntryPoint:!1,Events:[ua,Jt,Er,ic,oc,Zo]},Ln={findFiberByHostInstance:Lt,bundleType:0,version:"18.3.1",rendererPackageName:"react-dom"},jf={bundleType:Ln.bundleType,version:Ln.version,rendererPackageName:Ln.rendererPackageName,rendererConfig:Ln.rendererConfig,overrideHookState:null,overrideHookStateDeletePath:null,overrideHookStateRenamePath:null,overrideProps:null,overridePropsDeletePath:null,overridePropsRenamePath:null,setErrorHandler:null,setSuspenseHandler:null,scheduleUpdate:null,currentDispatcherRef:it.ReactCurrentDispatcher,findHostInstanceByFiber:function(e){return e=dc(e),e===null?null:e.stateNode},findFiberByHostInstance:Ln.findFiberByHostInstance||_f,findHostInstancesForRefresh:null,scheduleRefresh:null,scheduleRoot:null,setRefreshHandler:null,getCurrentFiber:null,reconcilerVersion:"18.3.1-next-f1338f8080-20240426"};if(typeof __REACT_DEVTOOLS_GLOBAL_HOOK__<"u"&&(_n=__REACT_DEVTOOLS_GLOBAL_HOOK__,!_n.isDisabled&&_n.supportsFiber))try{zr=_n.inject(jf),Ye=_n}catch{}var _n;Se.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED=$f;Se.createPortal=function(e,t){var n=2<arguments.length&&arguments[2]!==void 0?arguments[2]:null;if(!os(t))throw Error(b(200));return Lf(e,t,null,n)};Se.createRoot=function(e,t){if(!os(e))throw Error(b(299));var n=!1,a="",i=Vp;return t!=null&&(t.unstable_strictMode===!0&&(n=!0),t.identifierPrefix!==void 0&&(a=t.identifierPrefix),t.onRecoverableError!==void 0&&(i=t.onRecoverableError)),t=as(e,1,!1,null,null,n,!1,a,i),e[nt]=t.current,ta(e.nodeType===8?e.parentNode:e),new is(t)};Se.findDOMNode=function(e){if(e==null)return null;if(e.nodeType===1)return e;var t=e._reactInternals;if(t===void 0)throw typeof e.render=="function"?Error(b(188)):(e=Object.keys(e).join(","),Error(b(268,e)));return e=dc(t),e=e===null?null:e.stateNode,e};Se.flushSync=function(e){return Bt(e)};Se.hydrate=function(e,t,n){if(!Dr(t))throw Error(b(200));return Or(null,e,t,!0,n)};Se.hydrateRoot=function(e,t,n){if(!os(e))throw Error(b(405));var a=n!=null&&n.hydratedSources||null,i=!1,o="",s=Vp;if(n!=null&&(n.unstable_strictMode===!0&&(i=!0),n.identifierPrefix!==void 0&&(o=n.identifierPrefix),n.onRecoverableError!==void 0&&(s=n.onRecoverableError)),t=Up(t,null,e,1,n??null,i,!1,o,s),e[nt]=t.current,ta(e),a)for(e=0;e<a.length;e++)n=a[e],i=n._getVersion,i=i(n._source),t.mutableSourceEagerHydrationData==null?t.mutableSourceEagerHydrationData=[n,i]:t.mutableSourceEagerHydrationData.push(n,i);return new jr(t)};Se.render=function(e,t,n){if(!Dr(t))throw Error(b(200));return Or(null,e,t,!1,n)};Se.unmountComponentAtNode=function(e){if(!Dr(e))throw Error(b(40));return e._reactRootContainer?(Bt(function(){Or(null,null,e,!1,function(){e._reactRootContainer=null,e[nt]=null})}),!0):!1};Se.unstable_batchedUpdates=Zo;Se.unstable_renderSubtreeIntoContainer=function(e,t,n,a){if(!Dr(n))throw Error(b(200));if(e==null||e._reactInternals===void 0)throw Error(b(38));return Or(e,t,n,!1,a)};Se.version="18.3.1-next-f1338f8080-20240426"});var qp=Ge((r0,Yp)=>{"use strict";function Hp(){if(!(typeof __REACT_DEVTOOLS_GLOBAL_HOOK__>"u"||typeof __REACT_DEVTOOLS_GLOBAL_HOOK__.checkDCE!="function"))try{__REACT_DEVTOOLS_GLOBAL_HOOK__.checkDCE(Hp)}catch(e){console.error(e)}}Hp(),Yp.exports=Wp()});var Gp=Ge(ss=>{"use strict";var Qp=qp();ss.createRoot=Qp.createRoot,ss.hydrateRoot=Qp.hydrateRoot;var i0});var Kp=Ge(Ar=>{"use strict";var Df=ba(),Of=Symbol.for("react.element"),Af=Symbol.for("react.fragment"),Ff=Object.prototype.hasOwnProperty,Bf=Df.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED.ReactCurrentOwner,Uf={key:!0,ref:!0,__self:!0,__source:!0};function Xp(e,t,n){var a,i={},o=null,s=null;n!==void 0&&(o=""+n),t.key!==void 0&&(o=""+t.key),t.ref!==void 0&&(s=t.ref);for(a in t)Ff.call(t,a)&&!Uf.hasOwnProperty(a)&&(i[a]=t[a]);if(e&&e.defaultProps)for(a in t=e.defaultProps,t)i[a]===void 0&&(i[a]=t[a]);return{$$typeof:Of,type:e,key:o,ref:s,props:i,_owner:Bf.current}}Ar.Fragment=Af;Ar.jsx=Xp;Ar.jsxs=Xp});var ls=Ge((l0,Jp)=>{"use strict";Jp.exports=Kp()});var ms=ga(ba(),1),rd=ga(Gp(),1),r=ga(ls(),1),{useState:I,useEffect:W,useRef:ot,useMemo:id,useCallback:Vf}=ms.default,od={createRoot:rd.createRoot};function Wf(e){let[t,n]=I(e),a=Vf((i,o)=>n(s=>({...s,[i]:o})),[]);return[t,a]}var R=({d:e,size:t=18,stroke:n=1.6,fill:a="none",style:i})=>(0,r.jsx)("svg",{width:t,height:t,viewBox:"0 0 24 24",fill:a,stroke:"currentColor",strokeWidth:n,strokeLinecap:"round",strokeLinejoin:"round",style:i,children:typeof e=="string"?(0,r.jsx)("path",{d:e}):e}),O={arrow:e=>(0,r.jsx)(R,{...e,d:"M5 12h14M13 6l6 6-6 6"}),arrowDown:e=>(0,r.jsx)(R,{...e,d:"M12 5v14M6 13l6 6 6-6"}),check:e=>(0,r.jsx)(R,{...e,d:"M5 13l4 4 10-10"}),spark:e=>(0,r.jsx)(R,{...e,d:"M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M5.6 18.4l2.8-2.8M15.6 8.4l2.8-2.8"}),bolt:e=>(0,r.jsx)(R,{...e,d:"M13 2L4 14h7l-1 8 9-12h-7l1-8z"}),cube:e=>(0,r.jsx)(R,{...e,d:"M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16zM3.27 6.96L12 12l8.73-5.04M12 22V12"}),server:e=>(0,r.jsx)(R,{...e,d:"M3 5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM3 16a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM7 6.5h.01M7 17.5h.01"}),layers:e=>(0,r.jsx)(R,{...e,d:"M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"}),palette:e=>(0,r.jsx)(R,{...e,d:"M12 2a10 10 0 1 0 5 18.66c1.66-.96 1-3.66-1-3.66h-2a2 2 0 0 1-2-2 2 2 0 0 1 2-2h3a4 4 0 0 0 4-4c0-3.866-4-7-9-7zM7.5 12.5a1 1 0 1 1 0-2 1 1 0 0 1 0 2zM12 7a1 1 0 1 1 0-2 1 1 0 0 1 0 2zM16.5 9.5a1 1 0 1 1 0-2 1 1 0 0 1 0 2z"}),versions:e=>(0,r.jsx)(R,{...e,d:"M12 3v18M5 8l7-5 7 5M5 16l7 5 7-5"}),send:e=>(0,r.jsx)(R,{...e,d:"M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"}),play:e=>(0,r.jsx)(R,{...e,d:"M6 4l14 8-14 8V4z",fill:"currentColor",stroke:"none"}),pause:e=>(0,r.jsx)(R,{...e,d:"M7 4h3v16H7zM14 4h3v16h-3z",fill:"currentColor",stroke:"none"}),globe:e=>(0,r.jsx)(R,{...e,d:"M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM2 12h20M12 2a15 15 0 0 1 0 20M12 2a15 15 0 0 0 0 20"}),shield:e=>(0,r.jsx)(R,{...e,d:"M12 2L4 6v6c0 5 3.5 9.5 8 10 4.5-.5 8-5 8-10V6l-8-4z"}),cards:e=>(0,r.jsx)(R,{...e,d:"M3 5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM7 9a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2z"}),brain:e=>(0,r.jsx)(R,{...e,d:"M12 3a3 3 0 0 0-3 3 3 3 0 0 0-3 3v3a3 3 0 0 0 3 3v3a3 3 0 0 0 3 3 3 3 0 0 0 3-3 3 3 0 0 0 3-3v-3a3 3 0 0 0-3-3V6a3 3 0 0 0-3-3z"}),lock:e=>(0,r.jsx)(R,{...e,d:"M5 11h14v10H5zM8 11V7a4 4 0 0 1 8 0v4"}),user:e=>(0,r.jsx)(R,{...e,d:"M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z"}),wallet:e=>(0,r.jsx)(R,{...e,d:"M3 7v12a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2H5a2 2 0 0 1 0-4h13M17 14h.01"}),settings:e=>(0,r.jsx)(R,{...e,d:"M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"}),github:e=>(0,r.jsx)(R,{...e,d:"M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22"}),copy:e=>(0,r.jsx)(R,{...e,d:"M9 5H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4M14 3h7v7M10 14L21 3"}),plus:e=>(0,r.jsx)(R,{...e,d:"M12 5v14M5 12h14"}),close:e=>(0,r.jsx)(R,{...e,d:"M18 6L6 18M6 6l12 12"}),chevDown:e=>(0,r.jsx)(R,{...e,d:"M6 9l6 6 6-6"}),chevRight:e=>(0,r.jsx)(R,{...e,d:"M9 6l6 6-6 6"}),star:e=>(0,r.jsx)(R,{...e,d:"M12 2l3 7 7 .6-5.3 4.6 1.6 7-6.3-3.8L5.7 21l1.6-7L2 9.6 9 9z"}),rocket:e=>(0,r.jsx)(R,{...e,d:"M4.5 16.5L3 21l4.5-1.5M14 7a3 3 0 1 0-3-3M21 3s-6 0-12 6l-3 6 3 3 6-3c6-6 6-12 6-12zM10 14l-4-4"}),code:e=>(0,r.jsx)(R,{...e,d:"M8 6l-6 6 6 6M16 6l6 6-6 6M14 4l-4 16"}),database:e=>(0,r.jsx)(R,{...e,d:"M12 8c5 0 9-1.34 9-3s-4-3-9-3-9 1.34-9 3 4 3 9 3zM3 5v6c0 1.66 4 3 9 3s9-1.34 9-3V5M3 11v6c0 1.66 4 3 9 3s9-1.34 9-3v-6"}),zap:e=>(0,r.jsx)(R,{...e,d:"M13 2L4 14h7l-1 8 9-12h-7l1-8z"}),history:e=>(0,r.jsx)(R,{...e,d:"M3 12a9 9 0 1 0 3-6.7M3 4v5h5M12 7v5l3 2"})};function sd(e={}){let t=ot(null),[n,a]=I(!1);return W(()=>{if(!t.current||n)return;let i=e.threshold??.15,o=()=>{if(!t.current)return;let l=t.current.getBoundingClientRect(),c=window.innerHeight;l.top<c*(1-i*.5)&&l.bottom>0&&a(!0)};o();let s=()=>requestAnimationFrame(o);return window.addEventListener("scroll",s,{passive:!0}),window.addEventListener("resize",s,{passive:!0}),()=>{window.removeEventListener("scroll",s),window.removeEventListener("resize",s)}},[n]),[t,n]}function ld(e,t=1600,n=0){let[a,i]=sd(),[o,s]=I(0);return W(()=>{if(!i)return;let l,c,p=f=>{c||(c=f);let g=Math.min(1,(f-c)/t),v=1-Math.pow(1-g,3);s(e*v),g<1&&(l=requestAnimationFrame(p))};return l=requestAnimationFrame(p),()=>cancelAnimationFrame(l)},[i,e,t]),[a,o.toFixed(n)]}function Hf({children:e,strength:t=.25,className:n="",block:a=!1,...i}){let o=ot(null);return(0,r.jsx)("span",{onMouseMove:f=>{let g=o.current;if(!g)return;let v=g.getBoundingClientRect(),y=(f.clientX-(v.left+v.width/2))*t,w=(f.clientY-(v.top+v.height/2))*t;g.style.transform=`translate(${y}px, ${w}px)`},onMouseLeave:()=>{let f=o.current;f&&(f.style.transform="")},style:{display:a?"block":"inline-block",position:"relative"},children:(0,r.jsx)("span",{ref:o,className:n,style:{display:a?"block":"inline-flex",transition:"transform .35s cubic-bezier(.2,.8,.2,1)"},...i,children:e})})}function Yf(){let[e,t]=I(0);return W(()=>{let n=()=>{let a=document.documentElement.scrollHeight-window.innerHeight;t(a>0?window.scrollY/a:0)};return window.addEventListener("scroll",n,{passive:!0}),n(),()=>window.removeEventListener("scroll",n)},[]),(0,r.jsx)("div",{style:{position:"fixed",left:0,right:0,top:0,height:2,background:"linear-gradient(90deg, var(--accent), #ff8a4d)",transformOrigin:"left",transform:`scaleX(${e})`,zIndex:100,transition:"transform .12s linear"}})}var _e=[{id:"typing",dur:2200,label:"\u0412\u044B \u043F\u0438\u0448\u0435\u0442\u0435 \u043F\u0440\u043E\u043C\u043F\u0442"},{id:"thinking",dur:900,label:"\u0410\u0433\u0435\u043D\u0442 \u0430\u043D\u0430\u043B\u0438\u0437\u0438\u0440\u0443\u0435\u0442"},{id:"building",dur:3400,label:"\u0410\u0433\u0435\u043D\u0442 \u0441\u043E\u0431\u0438\u0440\u0430\u0435\u0442 \u0441\u0430\u0439\u0442"},{id:"rendered",dur:1100,label:"\u0413\u043E\u0442\u043E\u0432\u043E \u2014 \u0432\u0435\u0440\u0441\u0438\u044F 1.0"},{id:"revising",dur:1700,label:"\u041F\u0440\u043E\u0441\u0438\u0442\u0435 \u0443\u043B\u0443\u0447\u0448\u0438\u0442\u044C"},{id:"updating",dur:2e3,label:"\u041F\u0440\u0430\u0432\u0438\u0442 \u0438\u043D\u0442\u0435\u0440\u0444\u0435\u0439\u0441"},{id:"v2",dur:1e3,label:"\u0412\u0435\u0440\u0441\u0438\u044F 1.1 \u0441\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u0430"},{id:"deploying",dur:1500,label:"\u0414\u0435\u043F\u043B\u043E\u0439 \u043D\u0430 \u0441\u0435\u0440\u0432\u0435\u0440"},{id:"deployed",dur:2200,label:"\u041E\u043F\u0443\u0431\u043B\u0438\u043A\u043E\u0432\u0430\u043D\u043E"}],ne=e=>_e.findIndex(t=>t.id===e);function Zp(e,t,n=38,a=0){let[i,o]=I("");return W(()=>{if(o(""),!t)return;let s=0,l=setInterval(()=>{s+=1,o(e.slice(0,s)),s>=e.length&&clearInterval(l)},n);return()=>clearInterval(l)},[e,t,n,a]),i}function qf({accent:e="#7c5cff"}){let[t,n]=I(0),[a,i]=I(!0),[o,s]=I(0);W(()=>{if(!a)return;let p=setTimeout(()=>{let f=t+1;f>=_e.length?(n(0),s(g=>g+1)):n(f)},_e[t].dur);return()=>clearTimeout(p)},[t,a]);let l=_e[t].id,c=(t+1)/_e.length;return(0,r.jsx)(im,{color:"rgba(124, 92, 255, 0.18)",size:560,intensity:1,children:(0,r.jsxs)("div",{className:"ap-wrap",children:[(0,r.jsx)("style",{children:`
        .ap-wrap { position: relative; }
        .ap-frame-tilt { perspective: 1400px; }
        /* ambient-glow halo behind frame, color-shifts by phase */
        .ap-wrap::after {
          content: ''; position: absolute;
          inset: -40px; border-radius: 36px;
          background: radial-gradient(circle at 50% 50%, var(--phase-glow, rgba(124,92,255,0.25)), transparent 60%);
          filter: blur(50px);
          z-index: 0;
          pointer-events: none;
          transition: background 1s ease;
        }
        .ap-phase-typing,   .ap-phase-thinking { --phase-glow: rgba(124, 92, 255, 0.25); }
        .ap-phase-building, .ap-phase-updating { --phase-glow: rgba(255, 154, 93, 0.35); }
        .ap-phase-rendered, .ap-phase-v2       { --phase-glow: rgba(92, 184, 255, 0.30); }
        .ap-phase-revising                     { --phase-glow: rgba(236, 92, 255, 0.30); }
        .ap-phase-deploying                    { --phase-glow: rgba(255, 210, 92, 0.40); }
        .ap-phase-deployed                     { --phase-glow: rgba(76, 217, 164, 0.55); }
        .ap-frame {
          background: var(--bg);
          border: 1px solid var(--line);
          border-radius: 18px;
          box-shadow: var(--shadow-lg);
          overflow: hidden;
          width: 100%;
          aspect-ratio: 16 / 10;
          min-height: 540px;
          display: grid;
          grid-template-rows: 44px 1fr 30px;
        }
        /* top bar */
        .ap-topbar {
          display: flex; align-items: center; justify-content: space-between;
          padding: 0 14px;
          background: var(--bg-2);
          border-bottom: 1px solid var(--line);
          font-size: 12.5px;
        }
        .ap-topbar-left { display: flex; align-items: center; gap: 12px; }
        .ap-lights { display: flex; gap: 6px; }
        .ap-lights span { width: 11px; height: 11px; border-radius: 50%; background: #e0e0dd; }
        .ap-project { display: flex; align-items: center; gap: 7px; color: var(--fg-2); font-weight: 500; }
        .ap-project .dot { width:6px; height:6px; border-radius:50%; background: var(--green); animation: pulse-dot 2s infinite; }
        .ap-topbar-right { display: flex; align-items: center; gap: 6px; }
        .ap-chip {
          display: inline-flex; align-items: center; gap: 6px;
          padding: 5px 10px; border-radius: 999px; font-size: 12px;
          background: var(--bg); border: 1px solid var(--line); color: var(--fg-2);
        }
        .ap-chip .mono { font-size: 11.5px; color: var(--fg); font-weight: 500; }
        .ap-chip-model { gap: 8px; padding-left: 8px; }
        .ap-chip-model .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--accent); animation: pulse-dot 2s infinite; }
        .ap-chip-model .usage { font-family: var(--mono); font-size: 11px; color: var(--muted); padding-left: 6px; border-left: 1px solid var(--line); }
        .ap-chip-model .usage b { color: var(--accent); font-weight: 700; }
        .ap-chip-btn {
          width: 28px; height: 28px; border-radius: 8px;
          background: transparent; border: 1px solid transparent;
          display: inline-flex; align-items: center; justify-content: center;
          color: var(--muted);
        }
        .ap-chip-btn:hover { background: var(--bg); border-color: var(--line); color: var(--fg); }

        /* columns */
        .ap-cols { display: grid; grid-template-columns: 290px 1fr 240px; min-height: 0; }
        .ap-col-agent, .ap-col-versions { background: var(--bg-2); display: flex; flex-direction: column; min-height: 0; }
        .ap-col-agent { border-right: 1px solid var(--line); }
        .ap-col-versions { border-left: 1px solid var(--line); }
        .ap-col-preview { background:
          radial-gradient(circle at 20% 0%, rgba(124,92,255,0.05), transparent 50%),
          repeating-linear-gradient(0deg, transparent 0 23px, rgba(0,0,0,0.018) 23px 24px),
          var(--bg-2);
          position: relative; min-height: 0; display:flex; align-items:center; justify-content:center; padding: 22px;
        }

        /* col headers */
        .ap-col-head {
          padding: 12px 14px 10px;
          font-size: 11px; letter-spacing: 0.05em; text-transform: uppercase;
          color: var(--muted-2); font-weight: 600;
          display: flex; align-items: center; justify-content: space-between;
          border-bottom: 1px solid var(--line);
        }
        .ap-col-head .badge {
          background: var(--bg); border: 1px solid var(--line);
          padding: 2px 7px; border-radius: 6px; color: var(--fg-2); letter-spacing: 0;
          text-transform: none; font-weight: 500; font-size: 10.5px;
        }

        /* agent chat */
        .ap-chat { flex:1; padding: 12px; overflow: hidden; display:flex; flex-direction:column; gap: 10px; }
        .ap-msg {
          background: var(--bg); border: 1px solid var(--line);
          border-radius: 12px; padding: 10px 12px; font-size: 13px; line-height: 1.45;
          animation: float-up .35s ease both;
        }
        .ap-msg.user { background: var(--accent); color: white; border-color: transparent; align-self: flex-end; max-width: 90%; }
        .ap-msg.user.draft { background: var(--bg); color: var(--fg); border: 1px dashed var(--line-2); align-self: stretch; max-width: 100%; }
        .ap-msg .meta { font-size: 10.5px; color: var(--muted-2); margin-bottom: 4px; display: flex; align-items: center; gap: 6px; }
        .ap-msg.user .meta { color: rgba(255,255,255,0.7); }
        .ap-tools { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 8px; }
        .ap-tool {
          font-size: 11px; padding: 3px 8px; border-radius: 6px;
          background: var(--bg-2); border: 1px solid var(--line); color: var(--muted);
          display: inline-flex; gap: 5px; align-items: center;
        }
        .ap-tool.done { color: var(--green); border-color: rgba(0,168,107,0.25); background: rgba(0,168,107,0.06); }
        .ap-tool.run { color: var(--accent); border-color: var(--accent-line); background: var(--accent-soft); }
        .ap-tool .spin { width:8px; height:8px; border-radius: 50%; border: 1.4px solid currentColor; border-right-color: transparent; animation: spin 0.8s linear infinite; }
        .ap-cursor { display: inline-block; width: 1px; height: 14px; background: currentColor; vertical-align: -2px; margin-left: 1px; animation: typing-cursor 0.8s steps(1) infinite; }

        .ap-chat-input {
          margin: 10px; padding: 10px 12px; background: var(--bg);
          border: 1px solid var(--line); border-radius: 10px;
          font-size: 13px; color: var(--muted-2);
          display: flex; align-items: center; gap: 10px;
        }
        .ap-chat-input .send {
          margin-left: auto; width: 26px; height: 26px;
          border-radius: 7px; background: var(--accent); color: white;
          display: inline-flex; align-items: center; justify-content: center;
        }

        /* preview window */
        .ap-preview-card {
          width: 100%; height: 100%; max-width: 100%;
          background: var(--bg); border: 1px solid var(--line);
          border-radius: 12px; overflow: hidden; box-shadow: var(--shadow-md);
          display: flex; flex-direction: column;
        }
        .ap-preview-url {
          height: 32px; background: var(--bg-2); border-bottom: 1px solid var(--line);
          display: flex; align-items: center; gap: 10px; padding: 0 12px;
        }
        .ap-preview-url .dots { display:flex; gap:5px; }
        .ap-preview-url .dots span { width: 9px; height: 9px; border-radius: 50%; background: #e0e0dd; }
        .ap-preview-url .url {
          flex: 1; height: 20px; border-radius: 5px; background: var(--bg);
          border: 1px solid var(--line);
          display:flex; align-items:center; padding: 0 8px;
          font-family: var(--mono); font-size: 11px; color: var(--muted);
          gap: 6px;
        }
        .ap-preview-url .url .lock-i { color: var(--green); }
        .ap-preview-body { flex: 1; overflow: hidden; position: relative; }
        .ap-shimmer {
          position: absolute; inset: 0; padding: 18px; display: flex; flex-direction: column; gap: 12px;
        }
        .ap-shimmer .bar {
          background: linear-gradient(90deg, var(--bg-3) 0%, var(--bg-2) 50%, var(--bg-3) 100%);
          background-size: 400px 100%; animation: shimmer 1.4s infinite linear;
          border-radius: 8px; height: 14px;
        }
        .ap-shimmer .bar.big { height: 28px; width: 60%; }
        .ap-shimmer .bar.med { width: 80%; }
        .ap-shimmer .bar.sm { width: 40%; }
        .ap-shimmer .bar.block { height: 90px; }

        /* status pill bottom-right of preview */
        .ap-status {
          position: absolute; left: 50%; bottom: 18px; transform: translateX(-50%);
          background: rgba(10,10,10,0.86); color: white;
          padding: 8px 14px; border-radius: 999px; font-size: 12px;
          display: inline-flex; align-items: center; gap: 8px;
          backdrop-filter: blur(8px);
          animation: float-up .35s ease both;
        }
        .ap-status .spin { width: 11px; height: 11px; border-radius:50%; border: 1.6px solid white; border-right-color: transparent; animation: spin .8s linear infinite; }
        .ap-status.ok { background: rgba(0,168,107,0.95); }

        /* versions */
        .ap-versions { flex:1; padding: 8px 10px; overflow: hidden; display:flex; flex-direction:column; gap: 6px; }
        .ap-version {
          padding: 10px 11px; border-radius: 10px;
          background: var(--bg); border: 1px solid var(--line);
          font-size: 12.5px; display:flex; flex-direction: column; gap: 4px;
          animation: float-up .35s ease both;
        }
        .ap-version.active {
          border-color: var(--accent-line);
          box-shadow: 0 0 0 3px var(--accent-soft);
          animation: ver-pop .55s cubic-bezier(.5,1.6,.3,1) both, ver-glow 2.2s ease-out;
        }
        @keyframes ver-pop {
          0%   { transform: scale(.94); }
          60%  { transform: scale(1.04); }
          100% { transform: scale(1); }
        }
        @keyframes ver-glow {
          0%   { box-shadow: 0 0 0 0   rgba(124,92,255,0.55), 0 0 0 0 var(--accent-soft); }
          40%  { box-shadow: 0 0 0 8px rgba(124,92,255,0),    0 0 0 3px var(--accent-soft); }
          100% { box-shadow: 0 0 0 8px rgba(124,92,255,0),    0 0 0 3px var(--accent-soft); }
        }
        .ap-version .row { display:flex; align-items:center; justify-content: space-between; }
        .ap-version .name { font-weight: 600; color: var(--fg); display:flex; align-items:center; gap:7px; }
        .ap-version .dot { width:7px; height:7px; border-radius:50%; background: var(--muted-2); }
        .ap-version.active .dot { background: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
        .ap-version .meta { color: var(--muted); font-size: 11.5px; }
        .ap-version .desc { color: var(--muted); font-size: 12px; line-height: 1.4; }
        .ap-version .tag {
          display:inline-block; padding: 1.5px 6px; border-radius: 4px;
          background: var(--bg-2); border: 1px solid var(--line);
          font-family: var(--mono); font-size: 10.5px; color: var(--fg-2);
        }
        .ap-version .url-live {
          margin-top: 2px; font-family: var(--mono); font-size: 10.5px;
          color: var(--accent); display: inline-flex; align-items:center; gap: 4px;
        }

        /* deploy widget bottom of versions col */
        .ap-deploy {
          margin: 10px; padding: 12px; border-radius: 11px;
          background: var(--bg);
          border: 1px solid var(--line);
        }
        .ap-deploy.glow { border-color: var(--accent-line); box-shadow: 0 0 0 4px var(--accent-soft); }
        .ap-deploy .title { font-size: 12px; font-weight: 600; color: var(--fg); display:flex; align-items:center; gap: 7px; }
        .ap-deploy .sub { font-size: 11.5px; color: var(--muted); margin-top: 4px; line-height: 1.4; }
        .ap-deploy-btn {
          margin-top: 9px; width: 100%; padding: 8px 10px;
          background: var(--panel-dark); color: white; border: 0; border-radius: 8px;
          font-size: 12px; font-weight: 600;
          display:inline-flex; align-items:center; justify-content:center; gap:6px;
          transition: background .2s;
        }
        .ap-deploy.glow .ap-deploy-btn { background: var(--accent); }

        /* playback controls */
        .ap-controls {
          position: absolute; left: 50%; transform: translateX(-50%);
          bottom: -52px;
          display:flex; align-items:center; gap: 14px;
          background: var(--bg); border: 1px solid var(--line);
          border-radius: 999px; padding: 7px 14px;
          box-shadow: var(--shadow-md);
          z-index: 4;
        }
        .ap-ctl-btn { background: transparent; border: 0; width: 26px; height: 26px; display:inline-flex; align-items:center; justify-content:center; color: var(--fg); border-radius:50%; }
        .ap-ctl-btn:hover { background: var(--bg-2); }
        .ap-track { width: 220px; height: 4px; border-radius: 999px; background: var(--bg-3); position: relative; overflow: hidden; }
        .ap-track .fill { position:absolute; left:0; top:0; bottom:0; background: var(--accent); transition: width .5s linear; }
        .ap-step-label { font-size: 12px; color: var(--muted); font-family: var(--mono); min-width: 180px; text-align: left; }
        /* step pips */
        .ap-pips { display: flex; gap: 4px; align-items: center; }
        .ap-pip {
          width: 18px; height: 4px; border-radius: 999px;
          background: var(--bg-3); transition: all .25s;
        }
        .ap-pip.past { background: var(--accent); opacity: 0.5; }
        .ap-pip.on   { background: var(--accent); width: 28px; box-shadow: 0 0 8px var(--accent); }
        .ap-ctl-skip {
          font-size: 16px; font-weight: 700; color: var(--accent) !important;
          background: var(--accent-soft) !important;
        }
        .ap-ctl-skip:hover { background: var(--accent) !important; color: white !important; }

        @media (max-width: 1024px) {
          .ap-cols { grid-template-columns: 240px 1fr 200px; }
          .ap-frame { aspect-ratio: 4/3; min-height: 580px; }
        }
        @media (max-width: 760px) {
          .ap-frame { aspect-ratio: auto; min-height: 0; }
          .ap-cols { grid-template-columns: 1fr; grid-template-rows: 240px 280px 220px; }
          .ap-col-agent { border-right: 0; border-bottom: 1px solid var(--line); }
          .ap-col-versions { border-left: 0; border-top: 1px solid var(--line); }
          .ap-controls { bottom: -56px; padding: 6px 10px; gap: 10px; }
          .ap-track { width: 140px; }
          .ap-step-label { display: none; }
        }

        /* ---- Status bar (IDE-like footer) ---- */
        .ap-statusbar {
          display: flex; align-items: center; gap: 0;
          background: var(--panel-dark); color: rgba(255,255,255,0.7);
          border-top: 1px solid var(--line);
          font-family: var(--mono); font-size: 11px;
          padding: 0 14px;
        }
        .ap-sb-item {
          display: inline-flex; align-items: center; gap: 6px;
          padding: 0 12px;
          height: 100%;
          color: rgba(255,255,255,0.65);
          border-right: 1px solid rgba(255,255,255,0.06);
        }
        .ap-sb-item:first-child { padding-left: 0; }
        .ap-sb-item.accent { color: var(--accent); }
        .ap-sb-item.green  { color: #4cb98b; }
        .ap-sb-item.right  { margin-left: auto; border-right: 0; border-left: 1px solid rgba(255,255,255,0.06); padding-right: 0; }
        .ap-sb-item b { color: white; font-weight: 700; }
        .ap-sb-dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
        .ap-sb-pulse { animation: pulse-dot 1.4s infinite; }
        .ap-sb-spin {
          width: 9px; height: 9px; border-radius: 50%;
          border: 1.4px solid currentColor; border-right-color: transparent;
          animation: spin .8s linear infinite;
        }
      `}),(0,r.jsx)(om,{max:2.5,perspective:1400,children:(0,r.jsxs)("div",{className:`ap-frame ap-phase-${l}`,children:[(0,r.jsx)(Qf,{step:l}),(0,r.jsxs)("div",{className:"ap-cols",children:[(0,r.jsx)(Gf,{step:t,cur:l,loopKey:o}),(0,r.jsx)(Xf,{step:t,cur:l,loopKey:o}),(0,r.jsx)(nm,{step:t,cur:l})]}),(0,r.jsx)(am,{step:t,cur:l,loopKey:o}),(0,r.jsx)("div",{style:{position:"absolute",left:"50%",top:"50%",pointerEvents:"none"},children:(0,r.jsx)(rm,{trigger:l==="deployed",count:36,duration:1600,colors:["#7c5cff","#a48aff","#4cd9a4","#ffd166","#5cb8ff","#ec4cb8"]})})]})}),(0,r.jsxs)("div",{className:"ap-controls",children:[(0,r.jsx)("button",{className:"ap-ctl-btn",onClick:()=>i(p=>!p),"aria-label":"Play/Pause",children:a?(0,r.jsx)(O.pause,{size:14}):(0,r.jsx)(O.play,{size:14})}),(0,r.jsx)("div",{className:"ap-pips",children:_e.map((p,f)=>(0,r.jsx)("span",{className:`ap-pip ${f===t?"on":f<t?"past":""}`},f))}),(0,r.jsxs)("div",{className:"ap-step-label",children:[`0${t+1}`.slice(-2)," / 0",_e.length," \xB7 ",_e[t].label]}),(0,r.jsx)("button",{className:"ap-ctl-btn ap-ctl-skip",onClick:()=>n(ne("deployed")),"aria-label":"Skip to deploy",title:"\u041F\u0440\u043E\u043C\u043E\u0442\u0430\u0442\u044C \u043A \u0434\u0435\u043F\u043B\u043E\u044E",children:"\u21A6"}),(0,r.jsx)("button",{className:"ap-ctl-btn",onClick:()=>{n(0),s(p=>p+1)},"aria-label":"Restart",children:(0,r.jsx)(O.history,{size:14})})]})]})})}function Qf({step:e}){return(0,r.jsxs)("div",{className:"ap-topbar",children:[(0,r.jsxs)("div",{className:"ap-topbar-left",children:[(0,r.jsxs)("div",{className:"ap-lights",children:[(0,r.jsx)("span",{}),(0,r.jsx)("span",{}),(0,r.jsx)("span",{})]}),(0,r.jsxs)("div",{className:"ap-project",children:[(0,r.jsx)("span",{className:"dot"}),(0,r.jsx)("span",{children:"cafe-polet"}),(0,r.jsx)("span",{style:{color:"var(--muted-2)"},children:"/"}),(0,r.jsx)("span",{style:{color:"var(--muted)"},children:"main"})]})]}),(0,r.jsxs)("div",{className:"ap-topbar-right",children:[(0,r.jsxs)("div",{className:"ap-chip ap-chip-model",children:[(0,r.jsx)("span",{className:"dot"}),(0,r.jsx)("span",{className:"mono",children:"claude 4.5"}),(0,r.jsxs)("span",{className:"usage",children:[(0,r.jsx)("b",{children:"12"}),"/50"]})]}),(0,r.jsxs)("div",{className:"ap-chip",children:[(0,r.jsx)(O.cube,{size:12}),(0,r.jsx)("span",{children:"\u0441\u0435\u0440\u0432\u0435\u0440 RU-1"})]}),(0,r.jsxs)("div",{className:"ap-chip",children:[(0,r.jsx)(O.wallet,{size:12}),(0,r.jsx)("span",{className:"mono",children:"12 480 \u20BD"})]}),(0,r.jsx)("button",{className:"ap-chip-btn",children:(0,r.jsx)(O.settings,{size:14})}),(0,r.jsx)("button",{className:"ap-chip-btn",children:(0,r.jsx)(O.user,{size:14})})]})]})}var ed="\u0421\u0434\u0435\u043B\u0430\u0439 \u043B\u0435\u043D\u0434\u0438\u043D\u0433 \u0434\u043B\u044F \u0433\u043E\u0440\u043E\u0434\u0441\u043A\u043E\u0439 \u043A\u043E\u0444\u0435\u0439\u043D\u0438 \xAB\u041F\u043E\u043B\u0451\u0442\xBB \u2014 \u0433\u0435\u0440\u043E\u0439, \u043C\u0435\u043D\u044E, \u043A\u043E\u043D\u0442\u0430\u043A\u0442\u044B, \u0442\u0451\u043F\u043B\u0430\u044F \u043F\u0430\u043B\u0438\u0442\u0440\u0430",td="\u0421\u0434\u0435\u043B\u0430\u0439 \u044F\u0440\u0447\u0435: \u0434\u043E\u0431\u0430\u0432\u044C \u0440\u0430\u0437\u0434\u0435\u043B \xAB\u041C\u0435\u043D\u044E\xBB \u0441 \u043A\u0430\u0440\u0442\u043E\u0447\u043A\u0430\u043C\u0438 \u0438 \u043A\u0440\u0443\u043F\u043D\u044B\u0435 \u0444\u043E\u0442\u043E";function Gf({step:e,cur:t,loopKey:n}){let a=t==="typing",i=Zp(ed,a,28,n),o=e>=ne("thinking"),s=e>=ne("thinking"),l=e>=ne("rendered"),c=t==="revising",p=Zp(td,c,30,n+100),f=e>=ne("updating"),g=e>=ne("updating"),v=e>=ne("v2");return(0,r.jsxs)("div",{className:"ap-col-agent",style:{position:"relative"},children:[(0,r.jsx)(lm,{active:t==="typing"||t==="revising"||t==="thinking"||t==="building"||t==="updating",color:"#7c5cff",count:6}),(0,r.jsxs)("div",{className:"ap-col-head",children:[(0,r.jsx)("span",{children:"\u0410\u0433\u0435\u043D\u0442"}),(0,r.jsx)("span",{className:"badge",children:"Claude 4.5 \xB7 code"})]}),(0,r.jsxs)("div",{className:"ap-chat",children:[a&&!o&&(0,r.jsxs)("div",{className:"ap-msg user draft",children:[(0,r.jsxs)("div",{className:"meta",children:[(0,r.jsx)(O.user,{size:10})," \u0432\u044B"]}),i,(0,r.jsx)("span",{className:"ap-cursor"})]}),o&&(0,r.jsxs)("div",{className:"ap-msg user",children:[(0,r.jsx)("div",{className:"meta",children:"\u0432\u044B \xB7 \u0442\u043E\u043B\u044C\u043A\u043E \u0447\u0442\u043E"}),ed]}),s&&(0,r.jsxs)("div",{className:"ap-msg",children:[(0,r.jsxs)("div",{className:"meta",children:[(0,r.jsx)("span",{style:{width:6,height:6,borderRadius:"50%",background:"var(--accent)",display:"inline-block"}}),"omnia \xB7 \u0430\u0433\u0435\u043D\u0442"]}),t==="thinking"?(0,r.jsxs)(r.Fragment,{children:["\u0410\u043D\u0430\u043B\u0438\u0437\u0438\u0440\u0443\u044E \u0437\u0430\u043F\u0440\u043E\u0441",(0,r.jsx)("span",{className:"ap-cursor"})]}):(0,r.jsx)(r.Fragment,{children:"\u041F\u043E\u043D\u044F\u043B. \u0421\u043E\u0431\u0435\u0440\u0443 \u043E\u0434\u043D\u043E\u0441\u0442\u0440\u0430\u043D\u0438\u0447\u043D\u044B\u0439 \u0441\u0430\u0439\u0442: hero, \u0441\u0435\u043A\u0446\u0438\u044F \xAB\u041E \u043D\u0430\u0441\xBB, \u043C\u0435\u043D\u044E \u0438 \u043A\u043E\u043D\u0442\u0430\u043A\u0442\u044B. \u041F\u0430\u043B\u0438\u0442\u0440\u0430 \u2014 \u043A\u0440\u0435\u043C\u043E\u0432\u044B\u0439 + \u043A\u043E\u0444\u0435\u0439\u043D\u043E-\u043A\u043E\u0440\u0438\u0447\u043D\u0435\u0432\u044B\u0439."}),e>=ne("thinking")&&(0,r.jsxs)("div",{className:"ap-tools",children:[(0,r.jsxs)("div",{className:`ap-tool ${t==="building"?"run":"done"}`,children:[t==="building"?(0,r.jsx)("span",{className:"spin"}):(0,r.jsx)(O.check,{size:10}),"next.js \xB7 routes"]}),(0,r.jsxs)("div",{className:`ap-tool ${t==="building"?"run":l?"done":""}`,children:[t==="building"?(0,r.jsx)("span",{className:"spin"}):l?(0,r.jsx)(O.check,{size:10}):null,"ui \xB7 4 \u0441\u0435\u043A\u0446\u0438\u0438"]}),(0,r.jsxs)("div",{className:`ap-tool ${l?"done":t==="building"?"run":""}`,children:[l?(0,r.jsx)(O.check,{size:10}):t==="building"?(0,r.jsx)("span",{className:"spin"}):null,"theme \xB7 warm"]})]})]}),c&&!f&&(0,r.jsxs)("div",{className:"ap-msg user draft",children:[(0,r.jsxs)("div",{className:"meta",children:[(0,r.jsx)(O.user,{size:10})," \u0432\u044B"]}),p,(0,r.jsx)("span",{className:"ap-cursor"})]}),f&&(0,r.jsxs)("div",{className:"ap-msg user",children:[(0,r.jsx)("div",{className:"meta",children:"\u0432\u044B \xB7 \u0441\u0435\u0439\u0447\u0430\u0441"}),td]}),g&&(0,r.jsxs)("div",{className:"ap-msg",children:[(0,r.jsxs)("div",{className:"meta",children:[(0,r.jsx)("span",{style:{width:6,height:6,borderRadius:"50%",background:"var(--accent)",display:"inline-block"}}),"omnia \xB7 \u0430\u0433\u0435\u043D\u0442"]}),v?(0,r.jsx)(r.Fragment,{children:"\u0413\u043E\u0442\u043E\u0432\u043E. \u0421\u043E\u0437\u0434\u0430\u043B \u0441\u0435\u043A\u0446\u0438\u044E \u0441 \u043A\u0430\u0440\u0442\u043E\u0447\u043A\u0430\u043C\u0438 \u0438 \u043E\u0431\u043D\u043E\u0432\u0438\u043B \u0444\u043E\u0442\u043E \u0432 hero. \u0421\u043E\u0445\u0440\u0430\u043D\u0438\u043B \u043A\u0430\u043A \u0432\u0435\u0440\u0441\u0438\u044E 1.1."}):(0,r.jsxs)(r.Fragment,{children:["\u0414\u043E\u0431\u0430\u0432\u043B\u044F\u044E \u0441\u0435\u043A\u0446\u0438\u044E \xAB\u041C\u0435\u043D\u044E\xBB \u0438\u0437 6 \u043F\u043E\u0437\u0438\u0446\u0438\u0439",(0,r.jsx)("span",{className:"ap-cursor"})]})]})]}),(0,r.jsxs)("div",{className:"ap-chat-input",children:[(0,r.jsx)(O.spark,{size:14,style:{color:"var(--accent)"}}),(0,r.jsx)("span",{children:"\u041E\u043F\u0438\u0448\u0438\u0442\u0435 \u0441\u043B\u0435\u0434\u0443\u044E\u0449\u0438\u0439 \u0448\u0430\u0433\u2026"}),(0,r.jsx)("div",{className:"send",children:(0,r.jsx)(O.send,{size:13})})]})]})}function Br(e,t,n){let[a,i]=I(0);return W(()=>{if(e!==t){i(0);return}let o,s,l=c=>{s||(s=c);let p=c-s;i(Math.min(1,p/n)),p<n&&(o=requestAnimationFrame(l))};return o=requestAnimationFrame(l),()=>cancelAnimationFrame(o)},[e,t,n]),a}function Xf({step:e,cur:t,loopKey:n}){let a=_e[ne("building")].dur,i=_e[ne("updating")].dur,o=Br("building",t,a),s=Br("updating",t,i),l=t==="building",c=t==="updating",p=e>=ne("rendered"),f=e>=ne("v2"),g=e>=ne("building"),v=l?o:p?1:0;return(0,r.jsx)("div",{className:"ap-col-preview",children:(0,r.jsxs)("div",{className:"ap-preview-card",children:[(0,r.jsxs)("div",{className:"ap-preview-url",children:[(0,r.jsxs)("div",{className:"dots",children:[(0,r.jsx)("span",{}),(0,r.jsx)("span",{}),(0,r.jsx)("span",{})]}),(0,r.jsxs)("div",{className:"url",children:[(0,r.jsx)(O.lock,{size:10,className:"lock-i",style:{color:"var(--green)"}}),"cafe-polet.omnia.app",(l||c)&&(0,r.jsxs)("span",{className:"url-live-build",children:[(0,r.jsx)("span",{className:"lb-dot"}),"live"]})]}),(0,r.jsxs)("div",{style:{display:"flex",gap:6,color:"var(--muted-2)"},children:[(0,r.jsx)(O.history,{size:12}),(0,r.jsx)(O.copy,{size:12})]})]}),(0,r.jsxs)("div",{className:"ap-preview-body",children:[!g&&(0,r.jsx)(Kf,{}),g&&(0,r.jsx)(Jf,{reveal:v,v2:f,updateP:c?s:f?1:0,isUpdating:c,loopKey:n}),t==="deploying"&&(0,r.jsxs)("div",{className:"ap-status",children:[(0,r.jsx)("span",{className:"spin"})," \u0414\u0435\u043F\u043B\u043E\u044E \u043D\u0430 ru-1.omnia.app\u2026"]}),t==="deployed"&&(0,r.jsxs)("div",{className:"ap-status ok",children:[(0,r.jsx)(O.check,{size:13})," \u041E\u043F\u0443\u0431\u043B\u0438\u043A\u043E\u0432\u0430\u043D\u043E \u2014 cafe-polet.app"]})]})]})})}function Kf(){return(0,r.jsxs)("div",{className:"ap-empty",children:[(0,r.jsx)("style",{children:`
        .ap-empty {
          position: absolute; inset: 0;
          display: flex; flex-direction: column; align-items: center; justify-content: center;
          gap: 14px; color: var(--muted-2);
        }
        .ap-empty .blob {
          width: 56px; height: 56px; border-radius: 16px;
          background: var(--bg-2);
          border: 1.5px dashed var(--line-2);
          display: inline-flex; align-items: center; justify-content: center;
          color: var(--accent);
          animation: pulse-dot 2.4s ease-in-out infinite;
        }
        .ap-empty .label { font-size: 13px; color: var(--muted); }
        .ap-empty .hint { font-size: 11px; color: var(--muted-2); font-family: var(--mono); }
      `}),(0,r.jsx)("div",{className:"blob",children:(0,r.jsx)(O.spark,{size:20})}),(0,r.jsx)("div",{className:"label",children:"\u041E\u043F\u0438\u0448\u0438\u0442\u0435 \u043F\u0440\u043E\u0435\u043A\u0442 \u0441\u043B\u0435\u0432\u0430 \u2014 \u0441\u0431\u043E\u0440\u043A\u0430 \u043D\u0430\u0447\u043D\u0451\u0442\u0441\u044F \u0441\u0440\u0430\u0437\u0443"}),(0,r.jsx)("div",{className:"hint",children:"\u2318 + enter \u2014 \u043E\u0442\u043F\u0440\u0430\u0432\u0438\u0442\u044C"})]})}function Jf({reveal:e,v2:t,updateP:n,isUpdating:a,loopKey:i}){let o=ot(null);W(()=>{let S=o.current;if(!S)return;let u,d=h=>{let N=S.getBoundingClientRect(),C=((h.clientX-N.left)/N.width-.5)*2,M=((h.clientY-N.top)/N.height-.5)*2;cancelAnimationFrame(u),u=requestAnimationFrame(()=>{S.style.setProperty("--bx",`${C*8}px`),S.style.setProperty("--by",`${M*8}px`),S.style.setProperty("--bx2",`${C*-14}px`),S.style.setProperty("--by2",`${M*-14}px`),S.style.setProperty("--bx3",`${C*18}px`),S.style.setProperty("--by3",`${M*18}px`)})},m=()=>{cancelAnimationFrame(u),S.style.setProperty("--bx","0px"),S.style.setProperty("--by","0px"),S.style.setProperty("--bx2","0px"),S.style.setProperty("--by2","0px"),S.style.setProperty("--bx3","0px"),S.style.setProperty("--by3","0px")};return S.addEventListener("mousemove",d),S.addEventListener("mouseleave",m),()=>{S.removeEventListener("mousemove",d),S.removeEventListener("mouseleave",m),cancelAnimationFrame(u)}},[]);let s=S=>1-Math.pow(1-S,3),l=S=>e>=S,c=S=>Math.max(0,Math.min(1,s((e-S)/.12))),p=S=>`translateY(${(1-c(S))*10}px)`,f="\u043A\u043E\u0444\u0435, \u043A\u043E\u0442\u043E\u0440\u044B\u0439 \u0437\u0430\u0441\u0442\u0430\u0432\u043B\u044F\u0435\u0442\xA0\u043E\u0441\u0442\u0430\u043D\u043E\u0432\u0438\u0442\u044C\u0441\u044F.",g=id(()=>Array.from(f),[]),v=Math.max(0,Math.min(1,(e-.1)/.25)),y=Math.floor(v*g.length),w=c(.55),k=[.74,.78,.82];return(0,r.jsxs)("div",{className:"cs-root",ref:o,children:[(0,r.jsx)("style",{children:`
        .cs-root {
          position: absolute; inset: 0;
          background:
            radial-gradient(120% 80% at 80% 0%, #fff4d8 0%, transparent 50%),
            radial-gradient(100% 70% at 0% 100%, #f2dcb3 0%, transparent 55%),
            linear-gradient(180deg, #fdf5e1 0%, #f5e6c4 100%);
          color: #2a1a0c;
          font-family: 'Onest', sans-serif;
          padding: 22px 28px;
          overflow: hidden;
          letter-spacing: -0.005em;
        }
        .cs-grain {
          position: absolute; inset: 0; pointer-events: none; opacity: 0.18;
          background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='1.4' numOctaves='2'/%3E%3C/filter%3E%3Crect width='200' height='200' filter='url(%23n)'/%3E%3C/svg%3E");
          mix-blend-mode: multiply;
        }
        .cs-bean {
          position: absolute; pointer-events: none;
          color: rgba(74, 46, 26, 0.12);
          transition: transform .5s cubic-bezier(.2,.8,.2,1);
          will-change: transform;
        }
        .cs-bean.b1 { top: 10%; right: 6%; transform: translate(var(--bx, 0), var(--by, 0)) rotate(20deg); }
        .cs-bean.b2 { bottom: 22%; left: 4%; transform: translate(var(--bx2, 0), var(--by2, 0)) rotate(-18deg); }
        .cs-bean.b3 { bottom: 10%; right: 22%; transform: translate(var(--bx3, 0), var(--by3, 0)) rotate(40deg); }

        .cs-nav {
          display: flex; align-items: center; justify-content: space-between;
          margin-bottom: 22px; padding-bottom: 14px;
          border-bottom: 1px solid rgba(58, 42, 28, 0.08);
          transition: opacity .4s ease, transform .4s ease;
        }
        .cs-logo { font-weight: 900; font-size: 18px; letter-spacing: -0.05em; display: inline-flex; align-items: center; gap: 7px; text-transform: lowercase; }
        .cs-logo .star { color: #c14a08; font-size: 16px; transform: rotate(-8deg); display:inline-block; }
        .cs-links { display: flex; gap: 16px; font-size: 11.5px; color: #6e5340; }
        .cs-links a { padding: 4px 2px; }
        .cs-links a.on { color: #3a2a1c; font-weight: 600; border-bottom: 1.5px solid #3a2a1c; }
        .cs-cta-mini {
          display: inline-flex; align-items: center; gap: 5px;
          padding: 6px 11px; border-radius: 999px;
          background: #3a2a1c; color: #fbf6ec;
          font-size: 11.5px; font-weight: 500;
        }
        .cs-cta-mini .pulse { width:6px; height:6px; border-radius:50%; background:#f5d97c; animation: pulse-dot 1.6s infinite; }

        .cs-hero {
          display: grid; grid-template-columns: 1.15fr 1fr; gap: 18px;
          align-items: center;
        }
        .cs-title {
          font-size: clamp(26px, 3.4vw, 44px); line-height: 0.92;
          font-weight: 900; letter-spacing: -0.05em;
          margin: 0 0 16px;
          color: #1a0e04;
          text-wrap: balance;
        }
        .cs-title .char {
          display: inline-block;
          opacity: 0; transform: translateY(6px);
          transition: opacity .25s ease, transform .25s ease;
        }
        .cs-title .char.in { opacity: 1; transform: translateY(0); }
        .cs-title .accent { color: #b8410a; }
        .cs-sub {
          font-size: 12.5px; line-height: 1.5; color: #5a4530;
          max-width: 32ch; margin-bottom: 14px;
          transition: opacity .5s ease, transform .5s ease;
        }
        .cs-cta-row {
          display: flex; gap: 8px; align-items: center;
          transition: opacity .5s ease, transform .5s ease;
        }
        .cs-cta {
          display: inline-flex; align-items: center; gap: 7px;
          padding: 9px 14px; border-radius: 999px;
          background: #2a1a0c; color: #fbf6ec;
          font-size: 12px; font-weight: 600;
          letter-spacing: -0.01em;
          box-shadow: 0 8px 16px -8px rgba(42, 26, 12, 0.5);
        }
        .cs-cta-ghost {
          font-size: 12px; color: #6e5340; padding: 8px 4px;
        }

        .cs-mug-stage {
          position: relative; aspect-ratio: 1/1;
          transition: opacity .55s ease, transform .55s ease;
        }
        .cs-mug-stage svg { width: 100%; height: 100%; }

        .cs-chips {
          display: flex; gap: 6px; margin-top: 14px;
          flex-wrap: wrap;
        }
        .cs-chip {
          font-size: 11px; color: #5a4530;
          padding: 4px 10px; border-radius: 999px;
          background: rgba(255, 255, 255, 0.6);
          border: 1px solid rgba(58, 42, 28, 0.12);
          display: inline-flex; align-items: center; gap: 5px;
          opacity: 0; transform: translateY(4px);
          transition: opacity .35s ease, transform .35s ease;
        }
        .cs-chip.in { opacity: 1; transform: translateY(0); }
        .cs-chip .swatch { width: 7px; height: 7px; border-radius: 50%; }

        /* ===== V2: menu mode ===== */
        .cs-menu-wrap { position: relative; }
        .cs-menu-head {
          display: flex; align-items: flex-end; justify-content: space-between;
          margin: 14px 0 10px;
        }
        .cs-menu-title { font-size: 20px; font-weight: 800; letter-spacing: -0.03em; line-height: 1; }
        .cs-menu-meta { font-size: 10.5px; color: #6e5340; }
        .cs-menu-grid {
          display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px;
        }
        .cs-menu-card {
          background: rgba(255,255,255,0.85);
          backdrop-filter: blur(8px);
          border: 1px solid rgba(58, 42, 28, 0.08);
          border-radius: 16px;
          padding: 12px;
          display: flex; flex-direction: column; gap: 8px;
          box-shadow: 0 6px 18px -8px rgba(42, 26, 12, 0.15), 0 1px 3px rgba(42, 26, 12, 0.06);
          opacity: 1;
          transition: transform .35s cubic-bezier(.2,.8,.2,1), box-shadow .3s;
        }
        .cs-menu-card:hover {
          transform: translateY(-3px);
          box-shadow: 0 14px 30px -10px rgba(42, 26, 12, 0.25);
        }
        @keyframes cs-card-in {
          from { opacity: 0; transform: translateY(8px) scale(0.96); }
          to   { opacity: 1; transform: translateY(0) scale(1); }
        }
        .cs-menu-grid .cs-menu-card { animation: cs-card-in .55s cubic-bezier(.2,.7,.2,1) both; }
        .cs-menu-card .pic {
          aspect-ratio: 4/3;
          border-radius: 12px;
          overflow: hidden;
          position: relative;
          background: var(--card-hue, #8b5a32);
        }
        .cs-menu-card .pic::before {
          content: ''; position: absolute; inset: 0;
          background:
            radial-gradient(circle at 30% 30%, rgba(255,255,255,0.35), transparent 50%),
            radial-gradient(circle at 70% 80%, rgba(0,0,0,0.25), transparent 60%);
          mix-blend-mode: overlay;
        }
        .cs-menu-card .pic .label {
          position: absolute; left: 8px; bottom: 8px;
          background: rgba(255,255,255,0.95); backdrop-filter: blur(8px);
          color: #2a1a0c;
          padding: 2px 7px; border-radius: 999px;
          font-size: 9px; font-weight: 700; text-transform: uppercase;
          letter-spacing: 0.06em;
        }
        .cs-menu-card .pic svg { width: 100%; height: 100%; display: block; position: relative; z-index: 1; }
        .cs-menu-card .nm { font-size: 13px; font-weight: 800; letter-spacing: -0.02em; line-height: 1.15; color: #1a0e04; }
        .cs-menu-card .d  { font-size: 10.5px; color: #6e5340; line-height: 1.35; flex: 1; }
        .cs-menu-card .row {
          display: flex; align-items: center; justify-content: space-between;
          padding-top: 4px;
        }
        .cs-menu-card .p  { font-size: 13px; font-weight: 800; color: #1a0e04; font-variant-numeric: tabular-nums; }
        .cs-menu-card .add {
          width: 26px; height: 26px; border-radius: 50%;
          background: #1a0e04; color: #fbf6ec;
          display: inline-flex; align-items: center; justify-content: center;
          font-size: 14px; font-weight: 700;
          transition: transform .2s, background .2s;
        }
        .cs-menu-card .add:hover { background: #c14a08; transform: scale(1.1); }

        /* edit cursor flying over the section being changed */
        .cs-cursor {
          position: absolute;
          pointer-events: none;
          z-index: 5;
          opacity: 0;
          transition: opacity .25s ease, top .9s cubic-bezier(.5,0,.3,1), left .9s cubic-bezier(.5,0,.3,1);
        }
        .cs-cursor.on { opacity: 1; }
        .cs-cursor svg { display: block; filter: drop-shadow(0 4px 10px rgba(124,92,255,0.4)); }
        .cs-cursor .tag {
          position: absolute; left: 18px; top: 18px;
          padding: 4px 10px 4px 8px; border-radius: 999px;
          background: var(--accent); color: white;
          font-size: 11px; font-weight: 600;
          font-family: var(--font);
          display: inline-flex; align-items: center; gap: 5px;
          white-space: nowrap;
          box-shadow: 0 8px 20px -6px rgba(124,92,255,0.55);
        }
        .cs-cursor .tag .pulse { width:6px; height:6px; border-radius:50%; background:white; animation: pulse-dot 1.2s infinite; }

        /* highlight ring around section being edited */
        .cs-edit-ring {
          position: absolute;
          border: 1.5px dashed var(--accent);
          border-radius: 12px;
          pointer-events: none;
          z-index: 3;
          opacity: 0;
          transition: opacity .3s ease, top .9s cubic-bezier(.5,0,.3,1), left .9s cubic-bezier(.5,0,.3,1), width .9s cubic-bezier(.5,0,.3,1), height .9s cubic-bezier(.5,0,.3,1);
          background: rgba(124, 92, 255, 0.04);
        }
        .cs-edit-ring.on { opacity: 1; animation: edit-pulse 1.6s ease-in-out infinite; }
        @keyframes edit-pulse {
          0%,100% { box-shadow: 0 0 0 0 rgba(124,92,255,0.25); }
          50%     { box-shadow: 0 0 0 6px rgba(124,92,255,0); }
        }

        .url-live-build {
          margin-left: auto; display: inline-flex; align-items: center; gap: 5px;
          padding: 1px 7px 1px 5px; border-radius: 999px;
          background: rgba(255,107,53,0.1); color: #c14a08;
          font-family: var(--font); font-size: 10px; font-weight: 600;
          letter-spacing: 0.02em;
        }
        .url-live-build .lb-dot { width:6px; height:6px; border-radius:50%; background:#c14a08; animation: pulse-dot 1s infinite; }

        .cs-deploy-banner {
          position: absolute; left: 50%; bottom: 14px; transform: translateX(-50%);
          padding: 8px 14px 8px 10px;
          background: rgba(0, 168, 107, 0.95); color: white;
          border-radius: 999px; font-size: 12px; font-weight: 500;
          display: inline-flex; gap: 7px; align-items: center;
          box-shadow: 0 10px 28px -8px rgba(0, 168, 107, 0.4);
          z-index: 4;
          animation: float-up .4s ease;
        }
      `}),(0,r.jsx)("div",{className:"cs-grain"}),(0,r.jsx)(cs,{className:"cs-bean b1",size:36,opacity:c(.92)}),(0,r.jsx)(cs,{className:"cs-bean b2",size:28,opacity:c(.94)}),(0,r.jsx)(cs,{className:"cs-bean b3",size:22,opacity:c(.96)}),(0,r.jsxs)("div",{className:"cs-nav",style:{opacity:c(.04),transform:p(.04)},children:[(0,r.jsxs)("div",{className:"cs-logo",children:[(0,r.jsx)("span",{className:"star",children:"\u2726"}),"\u043F\u043E\u043B\u0451\u0442"]}),(0,r.jsxs)("div",{className:"cs-links",children:[(0,r.jsx)("a",{children:"\u043E \u043D\u0430\u0441"}),(0,r.jsx)("a",{className:t?"on":"",children:"\u043C\u0435\u043D\u044E"}),(0,r.jsx)("a",{children:"\u0433\u0434\u0435 \u043D\u0430\u0439\u0442\u0438"}),(0,r.jsx)("a",{children:"\u043A\u043E\u043D\u0442\u0430\u043A\u0442\u044B"})]}),(0,r.jsxs)("div",{className:"cs-cta-mini",children:[(0,r.jsx)("span",{className:"pulse"}),"\u0437\u0430\u0431\u0440\u043E\u043D\u0438\u0440\u043E\u0432\u0430\u0442\u044C"]})]}),!t&&(0,r.jsxs)("div",{className:"cs-hero",style:{opacity:a?.45:1,transition:"opacity .4s"},children:[(0,r.jsxs)("div",{children:[(0,r.jsx)("h2",{className:"cs-title",children:g.map((S,u)=>(0,r.jsx)("span",{className:`char ${u<y?"in":""}`,children:S===" "||S==="\xA0"?"\xA0":S},u))}),(0,r.jsx)("p",{className:"cs-sub",style:{opacity:c(.45),transform:p(.45)},children:"\u041C\u0430\u043B\u0435\u043D\u044C\u043A\u0430\u044F \u043E\u0431\u0436\u0430\u0440\u043A\u0430 \u0432 \u0446\u0435\u043D\u0442\u0440\u0435 \u0433\u043E\u0440\u043E\u0434\u0430. \u0417\u0451\u0440\u043D\u0430 \u0438\u0437\xA0\u042D\u0444\u0438\u043E\u043F\u0438\u0438, \u041A\u043E\u043B\u0443\u043C\u0431\u0438\u0438 \u0438\xA0\u041A\u0435\u043D\u0438\u0438 \u2014 \u043A\u0430\u0436\u0434\u0443\u044E \u043D\u0435\u0434\u0435\u043B\u044E \u0441\u0432\u0435\u0436\u0438\u0435."}),(0,r.jsxs)("div",{className:"cs-cta-row",style:{opacity:c(.58),transform:p(.58)},children:[(0,r.jsxs)("span",{className:"cs-cta",children:["\u0437\u0430\u0431\u0440\u043E\u043D\u0438\u0440\u043E\u0432\u0430\u0442\u044C \u0441\u0442\u043E\u043B\u0438\u043A ",(0,r.jsx)(O.arrow,{size:11})]}),(0,r.jsx)("span",{className:"cs-cta-ghost",children:"\u0441\u043C\u043E\u0442\u0440\u0435\u0442\u044C \u043C\u0435\u043D\u044E \u2192"})]}),(0,r.jsx)("div",{className:"cs-chips",children:[{n:"filter coffee",c:"#8b5a32"},{n:"espresso bar",c:"#2a1a0c"},{n:"bakery",c:"#d4a017"}].map((S,u)=>(0,r.jsxs)("span",{className:`cs-chip ${e>=k[u]?"in":""}`,style:{transitionDelay:`${u*80}ms`},children:[(0,r.jsx)("span",{className:"swatch",style:{background:S.c}}),S.n]},u))})]}),(0,r.jsx)("div",{className:"cs-mug-stage",style:{opacity:w,transform:`scale(${.85+w*.15})`},children:(0,r.jsx)(Zf,{p:w})})]}),t&&(0,r.jsx)(em,{loopKey:i}),a&&(0,r.jsxs)(r.Fragment,{children:[(0,r.jsx)("div",{className:"cs-edit-ring on",style:{top:n<.4?56:110,left:20,right:20,height:n<.4?100:240}}),(0,r.jsxs)("div",{className:"cs-cursor on",style:{top:n<.4?60:150,left:n<.4?"40%":"55%"},children:[(0,r.jsx)("svg",{width:"22",height:"22",viewBox:"0 0 22 22",fill:"none",children:(0,r.jsx)("path",{d:"M3 3 L19 11 L11 13 L9 21 Z",fill:"white",stroke:"#7c5cff",strokeWidth:"1.6",strokeLinejoin:"round"})}),(0,r.jsxs)("div",{className:"tag",children:[(0,r.jsx)("span",{className:"pulse"}),"omnia \u043F\u0438\u0448\u0435\u0442"]})]})]})]})}function Zf({p:e=1}){return(0,r.jsxs)("svg",{viewBox:"0 0 200 200",preserveAspectRatio:"xMidYMid meet",children:[(0,r.jsxs)("defs",{children:[(0,r.jsxs)("radialGradient",{id:"cs-coffee",cx:"0.4",cy:"0.35",children:[(0,r.jsx)("stop",{offset:"0%",stopColor:"#a06a3a"}),(0,r.jsx)("stop",{offset:"55%",stopColor:"#5e3a1c"}),(0,r.jsx)("stop",{offset:"100%",stopColor:"#2a160a"})]}),(0,r.jsxs)("linearGradient",{id:"cs-cup",x1:"0",y1:"0",x2:"0",y2:"1",children:[(0,r.jsx)("stop",{offset:"0%",stopColor:"#fbf4e4"}),(0,r.jsx)("stop",{offset:"100%",stopColor:"#e8d8b8"})]}),(0,r.jsxs)("radialGradient",{id:"cs-saucer",cx:"0.5",cy:"0.4",children:[(0,r.jsx)("stop",{offset:"0%",stopColor:"#fbf4e4"}),(0,r.jsx)("stop",{offset:"100%",stopColor:"#d4be8b"})]})]}),(0,r.jsxs)("g",{opacity:.4*e,children:[(0,r.jsx)("path",{d:"M85 40 Q90 30 85 22 Q80 14 90 6",stroke:"#a87f4a",strokeWidth:"2",fill:"none",strokeLinecap:"round",children:(0,r.jsx)("animate",{attributeName:"opacity",values:"0;0.7;0",dur:"3s",repeatCount:"indefinite"})}),(0,r.jsx)("path",{d:"M105 38 Q100 28 110 20 Q120 12 110 4",stroke:"#a87f4a",strokeWidth:"2",fill:"none",strokeLinecap:"round",children:(0,r.jsx)("animate",{attributeName:"opacity",values:"0;0.7;0",dur:"3s",begin:"1s",repeatCount:"indefinite"})}),(0,r.jsx)("path",{d:"M125 42 Q120 32 130 24 Q140 16 130 8",stroke:"#a87f4a",strokeWidth:"2",fill:"none",strokeLinecap:"round",children:(0,r.jsx)("animate",{attributeName:"opacity",values:"0;0.6;0",dur:"3s",begin:"2s",repeatCount:"indefinite"})})]}),(0,r.jsx)("ellipse",{cx:"100",cy:"172",rx:"78",ry:"10",fill:"#000",opacity:"0.06"}),(0,r.jsx)("ellipse",{cx:"100",cy:"158",rx:"76",ry:"14",fill:"url(#cs-saucer)",stroke:"#b0915a",strokeWidth:"1"}),(0,r.jsx)("ellipse",{cx:"100",cy:"156",rx:"58",ry:"9",fill:"#e8d3a4"}),(0,r.jsx)("path",{d:"M40 90 Q40 88 42 88 L158 88 Q160 88 160 90 L154 138 Q152 152 100 152 Q48 152 46 138 Z",fill:"url(#cs-cup)",stroke:"#a8895a",strokeWidth:"1.4"}),(0,r.jsx)("path",{d:"M158 96 Q180 100 178 118 Q176 132 158 132",stroke:"#a8895a",strokeWidth:"4",fill:"none",strokeLinecap:"round"}),(0,r.jsx)("ellipse",{cx:"100",cy:"92",rx:"58",ry:"9",fill:"url(#cs-coffee)"}),(0,r.jsx)("ellipse",{cx:"100",cy:"91",rx:"58",ry:"9",fill:"none",stroke:"#3a1f0c",strokeWidth:"0.8",opacity:"0.4"}),(0,r.jsxs)("g",{opacity:e,children:[(0,r.jsx)("path",{d:"M75 92 Q100 86 125 92 Q120 96 100 96 Q80 96 75 92 Z",fill:"#f8e6c8",opacity:"0.85"}),(0,r.jsx)("path",{d:"M85 90 Q100 88 115 90",stroke:"#fbf4e4",strokeWidth:"0.8",fill:"none"})]}),(0,r.jsx)("ellipse",{cx:"78",cy:"89",rx:"14",ry:"2.5",fill:"#fff",opacity:"0.5"})]})}function cs({className:e,size:t=30,opacity:n=1}){return(0,r.jsxs)("svg",{className:e,width:t,height:t*1.5,viewBox:"0 0 30 45",style:{opacity:n,transition:"opacity .5s"},children:[(0,r.jsx)("ellipse",{cx:"15",cy:"22.5",rx:"11",ry:"20",fill:"currentColor"}),(0,r.jsx)("path",{d:"M 15 5 Q 18 22.5 15 40",stroke:"rgba(0,0,0,0.25)",strokeWidth:"1.4",fill:"none"})]})}function em({loopKey:e}){return(0,r.jsxs)("div",{className:"cs-menu-wrap",children:[(0,r.jsxs)("div",{className:"cs-menu-head",children:[(0,r.jsx)("div",{className:"cs-menu-title",children:"\u043D\u0430\u0448\u0435 \u043C\u0435\u043D\u044E"}),(0,r.jsx)("div",{className:"cs-menu-meta",children:"\u043E\u0431\u043D\u043E\u0432\u043B\u044F\u0435\u0442\u0441\u044F \u0435\u0436\u0435\u043D\u0435\u0434\u0435\u043B\u044C\u043D\u043E \xB7 \u0446\u0435\u043D\u044B \u0432 \u20BD"})]}),(0,r.jsx)("div",{className:"cs-menu-grid",children:[{n:"\u044D\u0444\u0438\u043E\u043F\u0438\u044F \u0438\u0440\u0433\u0430\u0447\u0435\u0444\u0444\u0435",d:"\u0444\u0438\u043B\u044C\u0442\u0440 \xB7 \u044F\u0433\u043E\u0434\u044B, \u0436\u0430\u0441\u043C\u0438\u043D",p:"380",kind:"filter",accent:"#a06a3a"},{n:"\u043A\u043E\u043B\u0443\u043C\u0431\u0438\u044F \u0443\u0438\u043B\u0430",d:"\u044D\u0441\u043F\u0440\u0435\u0441\u0441\u043E \xB7 \u0448\u043E\u043A\u043E\u043B\u0430\u0434",p:"320",kind:"espresso",accent:"#3a1f0c"},{n:"\u0444\u043B\u044D\u0442 \u0443\u0430\u0439\u0442",d:"\u043D\u0430 \u043E\u0432\u0441\u044F\u043D\u043E\u043C",p:"290",kind:"flatwhite",accent:"#caa37e"},{n:"\u043A\u0440\u0443\u0430\u0441\u0441\u0430\u043D",d:"\u043C\u0438\u043D\u0434\u0430\u043B\u044C\u043D\u044B\u0439",p:"180",kind:"croissant",accent:"#d4a017"},{n:"\u0447\u0438\u0437\u043A\u0435\u0439\u043A",d:"\u0444\u0438\u0441\u0442\u0430\u0448\u043A\u043E\u0432\u044B\u0439",p:"340",kind:"cheesecake",accent:"#8aa84a"},{n:"\u043A\u0438\u043D\u043E\u0430 \u0431\u043E\u0443\u043B",d:"\u0441 \u0430\u0432\u043E\u043A\u0430\u0434\u043E",p:"420",kind:"bowl",accent:"#6b8a4a"}].map((n,a)=>(0,r.jsxs)("div",{className:"cs-menu-card",style:{animationDelay:`${a*70}ms`,"--card-hue":n.accent},children:[(0,r.jsxs)("div",{className:"pic",style:{background:`linear-gradient(135deg, ${n.accent} 0%, ${n.accent}cc 100%)`},children:[(0,r.jsx)(tm,{kind:n.kind}),(0,r.jsx)("span",{className:"label",children:n.kind==="filter"?"\u0444\u0438\u043B\u044C\u0442\u0440":n.kind==="espresso"?"\u044D\u0441\u043F\u0440\u0435\u0441\u0441\u043E":n.kind==="flatwhite"?"\u043D\u0430 \u043C\u043E\u043B\u043E\u043A\u0435":n.kind==="croissant"||n.kind==="cheesecake"?"\u043F\u0435\u043A\u0430\u0440\u043D\u044F":"\u043A\u0443\u0445\u043D\u044F"})]}),(0,r.jsx)("div",{className:"nm",children:n.n}),(0,r.jsx)("div",{className:"d",children:n.d}),(0,r.jsxs)("div",{className:"row",children:[(0,r.jsxs)("span",{className:"p",children:[n.p," \u20BD"]}),(0,r.jsx)("span",{className:"add",children:"+"})]})]},a))})]},e)}function tm({kind:e}){switch(e){case"filter":return(0,r.jsxs)("svg",{viewBox:"0 0 80 60",preserveAspectRatio:"xMidYMid meet",children:[(0,r.jsx)("defs",{children:(0,r.jsxs)("linearGradient",{id:"ff-bg",x1:"0",y1:"0",x2:"0",y2:"1",children:[(0,r.jsx)("stop",{offset:"0",stopColor:"#fbf4e4"}),(0,r.jsx)("stop",{offset:"1",stopColor:"#e8d8b8"})]})}),(0,r.jsx)("rect",{width:"80",height:"60",fill:"url(#ff-bg)"}),(0,r.jsx)("path",{d:"M28 12 L52 12 L46 32 L34 32 Z",fill:"#fff",stroke:"#a8895a",strokeWidth:"1"}),(0,r.jsx)("path",{d:"M30 12 L50 12",stroke:"#c14a08",strokeWidth:"2"}),(0,r.jsx)("line",{x1:"40",y1:"34",x2:"40",y2:"40",stroke:"#5e3a1c",strokeWidth:"1.5"}),(0,r.jsx)("path",{d:"M26 42 L54 42 L52 56 L28 56 Z",fill:"#fff",opacity:"0.85",stroke:"#a8895a",strokeWidth:"1"}),(0,r.jsx)("ellipse",{cx:"40",cy:"46",rx:"12",ry:"2",fill:"#5e3a1c"}),(0,r.jsx)("rect",{x:"29",y:"46",width:"22",height:"8",fill:"#5e3a1c"})]});case"espresso":return(0,r.jsxs)("svg",{viewBox:"0 0 80 60",preserveAspectRatio:"xMidYMid meet",children:[(0,r.jsx)("defs",{children:(0,r.jsxs)("linearGradient",{id:"es-bg",x1:"0",y1:"0",x2:"0",y2:"1",children:[(0,r.jsx)("stop",{offset:"0",stopColor:"#fbf4e4"}),(0,r.jsx)("stop",{offset:"1",stopColor:"#e8d8b8"})]})}),(0,r.jsx)("rect",{width:"80",height:"60",fill:"url(#es-bg)"}),(0,r.jsx)("ellipse",{cx:"40",cy:"52",rx:"22",ry:"3",fill:"#caa37e"}),(0,r.jsx)("ellipse",{cx:"40",cy:"50",rx:"22",ry:"3.5",fill:"#fbf4e4",stroke:"#a8895a",strokeWidth:"0.8"}),(0,r.jsx)("path",{d:"M28 28 L52 28 L50 46 L30 46 Z",fill:"#fff",stroke:"#a8895a",strokeWidth:"1.2"}),(0,r.jsx)("path",{d:"M52 32 Q58 33 58 38 Q58 43 52 43",fill:"none",stroke:"#a8895a",strokeWidth:"1.5"}),(0,r.jsx)("ellipse",{cx:"40",cy:"29",rx:"11",ry:"2",fill:"#2a160a"}),(0,r.jsx)("ellipse",{cx:"40",cy:"28.5",rx:"10",ry:"1.5",fill:"#a06a3a"})]});case"flatwhite":return(0,r.jsxs)("svg",{viewBox:"0 0 80 60",preserveAspectRatio:"xMidYMid meet",children:[(0,r.jsx)("defs",{children:(0,r.jsxs)("linearGradient",{id:"fw-bg",x1:"0",y1:"0",x2:"0",y2:"1",children:[(0,r.jsx)("stop",{offset:"0",stopColor:"#fbf4e4"}),(0,r.jsx)("stop",{offset:"1",stopColor:"#e8d8b8"})]})}),(0,r.jsx)("rect",{width:"80",height:"60",fill:"url(#fw-bg)"}),(0,r.jsx)("path",{d:"M26 18 L54 18 L50 52 L30 52 Z",fill:"#fbf4e4",stroke:"#a8895a",strokeWidth:"1.2",opacity:"0.95"}),(0,r.jsx)("path",{d:"M30 24 L50 24 L48 36 L32 36 Z",fill:"#a06a3a"}),(0,r.jsx)("path",{d:"M30 22 L50 22 L49.5 25 Q40 23 30.5 25 Z",fill:"#f8e6c8"}),(0,r.jsx)("ellipse",{cx:"40",cy:"22",rx:"8",ry:"1",fill:"#fff"}),(0,r.jsx)("path",{d:"M36 22 Q40 24 44 22",stroke:"#caa37e",strokeWidth:"0.6",fill:"none"})]});case"croissant":return(0,r.jsxs)("svg",{viewBox:"0 0 80 60",preserveAspectRatio:"xMidYMid meet",children:[(0,r.jsxs)("defs",{children:[(0,r.jsxs)("linearGradient",{id:"cr-bg",x1:"0",y1:"0",x2:"0",y2:"1",children:[(0,r.jsx)("stop",{offset:"0",stopColor:"#fbf4e4"}),(0,r.jsx)("stop",{offset:"1",stopColor:"#e8d8b8"})]}),(0,r.jsxs)("linearGradient",{id:"cr-body",x1:"0",y1:"0",x2:"0",y2:"1",children:[(0,r.jsx)("stop",{offset:"0%",stopColor:"#f4c47a"}),(0,r.jsx)("stop",{offset:"60%",stopColor:"#d49845"}),(0,r.jsx)("stop",{offset:"100%",stopColor:"#a86d22"})]})]}),(0,r.jsx)("rect",{width:"80",height:"60",fill:"url(#cr-bg)"}),(0,r.jsx)("ellipse",{cx:"40",cy:"48",rx:"28",ry:"4",fill:"#caa37e",opacity:"0.5"}),(0,r.jsx)("path",{d:"M18 38 Q30 12 50 16 Q66 18 64 38 Q60 42 50 36 Q40 42 30 38 Q22 40 18 38 Z",fill:"url(#cr-body)",stroke:"#8b5a22",strokeWidth:"1"}),(0,r.jsx)("path",{d:"M26 32 Q32 26 38 28",stroke:"#8b5a22",strokeWidth:"0.8",fill:"none",opacity:"0.7"}),(0,r.jsx)("path",{d:"M40 26 Q48 22 56 28",stroke:"#8b5a22",strokeWidth:"0.8",fill:"none",opacity:"0.7"}),(0,r.jsx)("path",{d:"M30 36 Q40 32 50 36",stroke:"#8b5a22",strokeWidth:"0.6",fill:"none",opacity:"0.6"}),(0,r.jsx)("ellipse",{cx:"40",cy:"22",rx:"3",ry:"1.5",fill:"#fbf4e4",stroke:"#a86d22",strokeWidth:"0.5"})]});case"cheesecake":return(0,r.jsxs)("svg",{viewBox:"0 0 80 60",preserveAspectRatio:"xMidYMid meet",children:[(0,r.jsx)("defs",{children:(0,r.jsxs)("linearGradient",{id:"ch-bg",x1:"0",y1:"0",x2:"0",y2:"1",children:[(0,r.jsx)("stop",{offset:"0",stopColor:"#fbf4e4"}),(0,r.jsx)("stop",{offset:"1",stopColor:"#e8d8b8"})]})}),(0,r.jsx)("rect",{width:"80",height:"60",fill:"url(#ch-bg)"}),(0,r.jsx)("ellipse",{cx:"40",cy:"52",rx:"28",ry:"3",fill:"#caa37e",opacity:"0.4"}),(0,r.jsx)("polygon",{points:"40,18 64,46 16,46",fill:"#e8e09a"}),(0,r.jsx)("polygon",{points:"16,46 64,46 60,52 20,52",fill:"#d4ca6a"}),(0,r.jsx)("polygon",{points:"20,52 60,52 58,55 22,55",fill:"#a86d22"}),(0,r.jsx)("circle",{cx:"30",cy:"30",r:"1.3",fill:"#8aa84a"}),(0,r.jsx)("circle",{cx:"46",cy:"32",r:"1.5",fill:"#8aa84a"}),(0,r.jsx)("circle",{cx:"40",cy:"38",r:"1.2",fill:"#8aa84a"}),(0,r.jsx)("circle",{cx:"36",cy:"24",r:"0.9",fill:"#8aa84a"}),(0,r.jsx)("circle",{cx:"50",cy:"40",r:"1.1",fill:"#8aa84a"})]});case"bowl":return(0,r.jsxs)("svg",{viewBox:"0 0 80 60",preserveAspectRatio:"xMidYMid meet",children:[(0,r.jsx)("defs",{children:(0,r.jsxs)("linearGradient",{id:"bw-bg",x1:"0",y1:"0",x2:"0",y2:"1",children:[(0,r.jsx)("stop",{offset:"0",stopColor:"#fbf4e4"}),(0,r.jsx)("stop",{offset:"1",stopColor:"#e8d8b8"})]})}),(0,r.jsx)("rect",{width:"80",height:"60",fill:"url(#bw-bg)"}),(0,r.jsx)("ellipse",{cx:"40",cy:"46",rx:"26",ry:"6",fill:"#fbf4e4",stroke:"#a8895a",strokeWidth:"1"}),(0,r.jsx)("path",{d:"M14 46 Q40 60 66 46",fill:"#fbf4e4",stroke:"#a8895a",strokeWidth:"1"}),(0,r.jsx)("ellipse",{cx:"40",cy:"42",rx:"22",ry:"4",fill:"#e3c98a"}),(0,r.jsx)("ellipse",{cx:"28",cy:"38",rx:"6",ry:"3.5",fill:"#6b8a4a"}),(0,r.jsx)("ellipse",{cx:"28",cy:"38",rx:"4",ry:"2.5",fill:"#a3c073"}),(0,r.jsx)("circle",{cx:"28",cy:"38",r:"1.3",fill:"#4a5a2a"}),(0,r.jsx)("circle",{cx:"50",cy:"38",r:"3",fill:"#c14a08"}),(0,r.jsx)("circle",{cx:"49",cy:"37",r:"1",fill:"#e87a3a"}),(0,r.jsx)("path",{d:"M36 36 Q40 30 44 36",stroke:"#6b8a4a",strokeWidth:"1.6",fill:"none"})]});default:return null}}function nm({step:e,cur:t}){let n=e>=ne("rendered"),a=e>=ne("v2"),i=e>=ne("deployed"),o=t==="deploying",s=a&&!o,l=n&&!a;return(0,r.jsxs)("div",{className:"ap-col-versions",children:[(0,r.jsxs)("div",{className:"ap-col-head",children:[(0,r.jsx)("span",{children:"\u0412\u0435\u0440\u0441\u0438\u0438"}),(0,r.jsx)("span",{className:"badge",children:a?2:n?1:0})]}),(0,r.jsxs)("div",{className:"ap-versions",children:[a&&(0,r.jsxs)("div",{className:`ap-version ${s?"active":""}`,children:[(0,r.jsxs)("div",{className:"row",children:[(0,r.jsxs)("div",{className:"name",children:[(0,r.jsx)("span",{className:"dot"}),"v1.1"]}),(0,r.jsx)("span",{className:"tag",children:"+ \u043C\u0435\u043D\u044E"})]}),(0,r.jsx)("div",{className:"desc",children:"\u0434\u043E\u0431\u0430\u0432\u043B\u0435\u043D\u0430 \u0441\u0435\u043A\u0446\u0438\u044F \u043C\u0435\u043D\u044E, \u043E\u0431\u043D\u043E\u0432\u043B\u0451\u043D hero"}),(0,r.jsx)("div",{className:"meta",children:"\u0442\u043E\u043B\u044C\u043A\u043E \u0447\u0442\u043E \xB7 6 \u0444\u0430\u0439\u043B\u043E\u0432"}),i&&(0,r.jsxs)("div",{className:"url-live",children:[(0,r.jsx)(O.globe,{size:10}),"cafe-polet.app"]})]}),n&&(0,r.jsxs)("div",{className:`ap-version ${l?"active":""}`,children:[(0,r.jsxs)("div",{className:"row",children:[(0,r.jsxs)("div",{className:"name",children:[(0,r.jsx)("span",{className:"dot"}),"v1.0"]}),(0,r.jsx)("span",{className:"tag",children:"initial"})]}),(0,r.jsx)("div",{className:"desc",children:"\u043F\u0435\u0440\u0432\u0430\u044F \u0432\u0435\u0440\u0441\u0438\u044F \u043B\u0435\u043D\u0434\u0438\u043D\u0433\u0430"}),(0,r.jsxs)("div",{className:"meta",children:[a?"2 \u043C\u0438\u043D \u043D\u0430\u0437\u0430\u0434":"\u0442\u043E\u043B\u044C\u043A\u043E \u0447\u0442\u043E"," \xB7 4 \u0444\u0430\u0439\u043B\u0430"]})]}),!n&&(0,r.jsxs)("div",{style:{padding:"14px 8px",color:"var(--muted-2)",fontSize:12,textAlign:"center",lineHeight:1.5},children:["\u0418\u0441\u0442\u043E\u0440\u0438\u044F \u043F\u043E\u044F\u0432\u0438\u0442\u0441\u044F \u043F\u043E\u0441\u043B\u0435",(0,r.jsx)("br",{}),"\u043F\u0435\u0440\u0432\u043E\u0439 \u0433\u0435\u043D\u0435\u0440\u0430\u0446\u0438\u0438"]})]}),(0,r.jsxs)("div",{className:`ap-deploy ${t==="deploying"||t==="deployed"?"glow":""}`,children:[(0,r.jsxs)("div",{className:"title",children:[(0,r.jsx)(O.rocket,{size:12,style:{color:"var(--accent)"}})," \u0414\u0435\u043F\u043B\u043E\u0439"]}),(0,r.jsx)("div",{className:"sub",children:i?"Live \xB7 \u0441\u0435\u0440\u0432\u0435\u0440 RU-1, \u0433\u043E\u0442\u043E\u0432 \u043A \u043D\u0430\u0433\u0440\u0443\u0437\u043A\u0435":o?"\u0421\u0431\u043E\u0440\u043A\u0430, \u043F\u0435\u0440\u0435\u043D\u043E\u0441 \u043D\u0430 \u0441\u0435\u0440\u0432\u0435\u0440 RU-1 (\u041C\u043E\u0441\u043A\u0432\u0430)\u2026":"\u0412 \u043E\u0434\u0438\u043D \u043A\u043B\u0438\u043A. \u0412\u044B\u0434\u0435\u043B\u0435\u043D\u043D\u044B\u0439 \u0441\u0435\u0440\u0432\u0435\u0440."}),(0,r.jsx)("button",{className:"ap-deploy-btn",children:i?(0,r.jsxs)(r.Fragment,{children:[(0,r.jsx)(O.check,{size:11})," \u041E\u043F\u0443\u0431\u043B\u0438\u043A\u043E\u0432\u0430\u043D\u043E"]}):o?(0,r.jsxs)(r.Fragment,{children:[(0,r.jsx)("span",{style:{width:9,height:9,borderRadius:"50%",border:"1.5px solid white",borderRightColor:"transparent",display:"inline-block",animation:"spin .8s linear infinite"}}),"\u0414\u0435\u043F\u043B\u043E\u044E\u2026"]}):(0,r.jsxs)(r.Fragment,{children:[(0,r.jsx)(O.bolt,{size:11})," \u041E\u043F\u0443\u0431\u043B\u0438\u043A\u043E\u0432\u0430\u0442\u044C v",a?"1.1":"1.0"]})})]})]})}function am({step:e,cur:t,loopKey:n}){let a=_e[ne("building")].dur,i=_e[ne("updating")].dur,o=Br("building",t,a),s=Br("updating",t,i),l=["app/page.tsx","app/layout.tsx","components/Hero.tsx","components/Nav.tsx","styles/theme.css","lib/db.ts"],c=["components/Menu.tsx","data/menu.ts","app/page.tsx"],p=null,f=0,g=0,v="main";if(t==="typing"||t==="thinking")p=t==="thinking"?"\u043E\u0431\u0434\u0443\u043C\u044B\u0432\u0430\u044E \u0430\u0440\u0445\u0438\u0442\u0435\u043A\u0442\u0443\u0440\u0443":"\u0436\u0434\u0443 \u043F\u0440\u043E\u043C\u043F\u0442";else if(t==="building"){let k=Math.floor(o*l.length);p=`\u043F\u0438\u0448\u0443 ${l[Math.min(k,l.length-1)]}`,f=Math.min(l.length,k+1),g=Math.floor(o*480)}else if(t==="rendered")p="\u0433\u043E\u0442\u043E\u0432\u043E \xB7 4 \u0441\u0442\u0440\u0430\u043D\u0438\u0446\u044B, 1 \u0431\u0430\u0437\u0430",f=l.length,g=480;else if(t==="revising")p="\u0436\u0434\u0443 \u0443\u0442\u043E\u0447\u043D\u0435\u043D\u0438\u044F",f=l.length,g=480;else if(t==="updating"){let k=Math.floor(s*c.length);p=`\u043E\u0431\u043D\u043E\u0432\u043B\u044F\u044E ${c[Math.min(k,c.length-1)]}`,f=l.length+1,g=480+Math.floor(s*140),v="main *"}else t==="v2"?(p="\u043A\u043E\u043C\u043C\u0438\u0442 \u0441\u043E\u0445\u0440\u0430\u043D\u0451\u043D",f=l.length+1,g=620):t==="deploying"?(p="\u0434\u0435\u043F\u043B\u043E\u0439 \u2192 ru-1.omnia.app",f=l.length+1,g=620):t==="deployed"&&(p="\u043E\u043F\u0443\u0431\u043B\u0438\u043A\u043E\u0432\u0430\u043D\u043E \xB7 cafe-polet.app",f=l.length+1,g=620);let y=t==="building"||t==="updating"||t==="deploying",w=t==="deployed";return(0,r.jsxs)("div",{className:"ap-statusbar",children:[(0,r.jsxs)("div",{className:`ap-sb-item ${w?"green":y?"accent":""}`,children:[y?(0,r.jsx)("span",{className:"ap-sb-spin"}):(0,r.jsx)("span",{className:"ap-sb-dot ap-sb-pulse"}),p]}),f>0&&(0,r.jsxs)(r.Fragment,{children:[(0,r.jsxs)("div",{className:"ap-sb-item",children:[(0,r.jsx)(O.code,{size:11}),(0,r.jsx)("b",{children:f})," \u0444\u0430\u0439\u043B",f>1?"\u043E\u0432":""]}),(0,r.jsxs)("div",{className:"ap-sb-item",children:[(0,r.jsx)("b",{style:{fontVariantNumeric:"tabular-nums"},children:g.toLocaleString("ru-RU")})," \u0441\u0442\u0440\u043E\u043A"]})]}),(0,r.jsxs)("div",{className:"ap-sb-item right",children:[(0,r.jsx)(O.versions,{size:11}),v]}),(0,r.jsx)("div",{className:"ap-sb-item",children:"next.js \xB7 postgres"}),(0,r.jsxs)("div",{className:"ap-sb-item",children:[(0,r.jsx)("span",{className:"ap-sb-dot",style:{background:w?"#4cb98b":y?"var(--accent)":"#4cb98b"}}),w?"live":y?"building":"ready"]})]})}function wn(){let[e,t]=I(!1);return W(()=>{let n=window.matchMedia("(prefers-reduced-motion: reduce)");t(n.matches);let a=()=>t(n.matches);return n.addEventListener?.("change",a),()=>n.removeEventListener?.("change",a)},[]),e}function rm({trigger:e,count:t=24,colors:n=["#7c5cff","#a48aff","#4cd9a4","#ffd166","#ff6b8a"],duration:a=1400,originY:i="50%",originX:o="50%"}){let[s,l]=I([]),c=wn(),p=ot(!1);return W(()=>{if(!c){if(e&&!p.current){let f=Date.now(),g=Array.from({length:t},(v,y)=>{let w=Math.PI*2*y/t+(Math.random()-.5)*.4,k=80+Math.random()*140,S=Math.cos(w)*k,u=Math.sin(w)*k,d=n[y%n.length],m=5+Math.random()*6,h=(Math.random()-.5)*720,N=a*(.7+Math.random()*.5);return{i:`${f}-${y}`,dx:S,dy:u,c:d,size:m,rotEnd:h,dur:N}});l(v=>[...v,{id:f,particles:g}]),setTimeout(()=>l(v=>v.filter(y=>y.id!==f)),a+300)}p.current=e}},[e,c,t,a,n]),c?null:(0,r.jsxs)("div",{className:"pb-host",style:{left:o,top:i},children:[(0,r.jsx)("style",{children:`
        .pb-host {
          position: absolute; width: 0; height: 0;
          pointer-events: none; z-index: 50;
        }
        .pb-p {
          position: absolute; left: 0; top: 0;
          border-radius: 2px;
          will-change: transform, opacity;
          animation: pb-fly forwards cubic-bezier(.25, .8, .35, 1);
        }
        @keyframes pb-fly {
          0%   { transform: translate(0, 0) rotate(0) scale(.4); opacity: 0; }
          12%  { opacity: 1; transform: translate(calc(var(--dx) * .15), calc(var(--dy) * .15)) rotate(calc(var(--rot) * .15)) scale(1); }
          70%  { opacity: 1; }
          100% { transform: translate(calc(var(--dx) * 1), calc(var(--dy) * 1 + 60px)) rotate(var(--rot)) scale(.6); opacity: 0; }
        }
      `}),s.map(f=>f.particles.map(g=>(0,r.jsx)("span",{className:"pb-p",style:{width:g.size,height:g.size*.4,background:g.c,"--dx":`${g.dx}px`,"--dy":`${g.dy}px`,"--rot":`${g.rotEnd}deg`,animationDuration:`${g.dur}ms`}},g.i)))]})}function im({children:e,color:t="rgba(124, 92, 255, 0.15)",size:n=480,intensity:a=1,className:i=""}){let o=ot(null),s=wn();return W(()=>{if(s)return;let l=o.current;if(!l)return;let c=f=>{let g=l.getBoundingClientRect();l.style.setProperty("--sx",`${f.clientX-g.left}px`),l.style.setProperty("--sy",`${f.clientY-g.top}px`),l.style.setProperty("--sopa","1")},p=()=>l.style.setProperty("--sopa","0");return l.addEventListener("mousemove",c),l.addEventListener("mouseleave",p),()=>{l.removeEventListener("mousemove",c),l.removeEventListener("mouseleave",p)}},[s]),(0,r.jsxs)("div",{ref:o,className:`sl-host ${i}`,style:{"--sopa":0},children:[(0,r.jsx)("style",{children:`
        .sl-host {
          position: relative;
          --sx: 50%; --sy: 50%; --sopa: 0;
        }
        .sl-host::after {
          content: '';
          position: absolute; inset: 0;
          background: radial-gradient(${n}px circle at var(--sx) var(--sy), ${t}, transparent 60%);
          pointer-events: none;
          opacity: calc(var(--sopa) * ${a});
          transition: opacity .35s;
          mix-blend-mode: screen;
          border-radius: inherit;
          z-index: 4;
        }
      `}),e]})}function om({children:e,max:t=6,perspective:n=1200,scale:a=1,className:i=""}){let o=ot(null),s=wn();return W(()=>{if(s)return;let l=o.current;if(!l)return;let c,p=g=>{let v=l.getBoundingClientRect(),y=v.left+v.width/2,w=v.top+v.height/2,k=(g.clientX-y)/(v.width/2),S=(g.clientY-w)/(v.height/2);cancelAnimationFrame(c),c=requestAnimationFrame(()=>{l.style.transform=`perspective(${n}px) rotateX(${-S*t}deg) rotateY(${k*t}deg) scale(${a})`})},f=()=>{cancelAnimationFrame(c),l.style.transform=`perspective(${n}px) rotateX(0) rotateY(0) scale(1)`};return l.addEventListener("mousemove",p),l.addEventListener("mouseleave",f),()=>{l.removeEventListener("mousemove",p),l.removeEventListener("mouseleave",f),cancelAnimationFrame(c)}},[t,n,a,s]),(0,r.jsx)("div",{ref:o,className:`tilt-3d ${i}`,style:{transition:"transform .35s cubic-bezier(.2,.8,.2,1)",transformStyle:"preserve-3d",willChange:"transform"},children:e})}function sm({children:e,from:t=.94,to:n=1,className:a=""}){let i=ot(null),[o,s]=I(t),l=wn();return W(()=>{if(l){s(n);return}let c=i.current;if(!c)return;let p=()=>{let f=c.getBoundingClientRect(),g=window.innerHeight,v=Math.max(0,Math.min(1,1-f.top/(g*.85))),y=1-Math.pow(1-v,3);s(t+(n-t)*y)};return p(),window.addEventListener("scroll",p,{passive:!0}),window.addEventListener("resize",p,{passive:!0}),()=>{window.removeEventListener("scroll",p),window.removeEventListener("resize",p)}},[t,n,l]),(0,r.jsx)("div",{ref:i,className:a,style:{transform:`scale(${o})`,transformOrigin:"center top",transition:"transform .15s linear",willChange:"transform"},children:e})}function lm({active:e,color:t="#7c5cff",count:n=8}){let a=wn();return!e||a?null:(0,r.jsxs)("div",{className:"ct-host","aria-hidden":!0,children:[(0,r.jsx)("style",{children:`
        .ct-host { position: absolute; inset: 0; pointer-events: none; z-index: 6; }
        .ct-d {
          position: absolute; width: 6px; height: 6px; border-radius: 50%;
          background: ${t}; filter: blur(2px);
          opacity: 0;
          animation: ct-pulse 1.8s ease-out infinite;
        }
        @keyframes ct-pulse {
          0%   { opacity: 0; transform: scale(.4); }
          25%  { opacity: .6; transform: scale(1); }
          100% { opacity: 0; transform: scale(.2) translateY(-12px); }
        }
      `}),Array.from({length:n}).map((i,o)=>(0,r.jsx)("span",{className:"ct-d",style:{left:`${10+o*11%80}%`,top:`${20+o*17%60}%`,animationDelay:`${o*.22}s`}},o))]})}function ps({val:e,suf:t="",label:n,dec:a=0,dur:i=1700}){let[o,s]=ld(e,i,a),l=Number(s).toLocaleString("ru-RU",{minimumFractionDigits:a,maximumFractionDigits:a});return(0,r.jsxs)("div",{ref:o,children:[(0,r.jsxs)("b",{style:{fontVariantNumeric:"tabular-nums"},children:[l,t]}),n]})}function cm(){return(0,r.jsxs)("section",{className:"om-demo",children:[(0,r.jsx)("style",{children:`
        .om-demo {
          padding: 80px 0 140px;
          position: relative;
          overflow: hidden;
        }
        .om-demo::before {
          content: ''; position: absolute;
          left: 50%; top: 20%; transform: translateX(-50%);
          width: 100%; max-width: 1400px; aspect-ratio: 1.4/1;
          background:
            radial-gradient(circle at 30% 40%, rgba(124,92,255,0.35), transparent 55%),
            radial-gradient(circle at 70% 60%, rgba(92,184,255,0.22), transparent 55%),
            radial-gradient(circle at 50% 80%, rgba(236,92,255,0.18), transparent 55%);
          filter: blur(80px);
          pointer-events: none;
          z-index: 0;
          animation: demo-orb 12s ease-in-out infinite;
        }
        @keyframes demo-orb {
          0%,100% { transform: translateX(-50%) scale(1) rotate(0deg); }
          50%     { transform: translateX(-50%) scale(1.08) rotate(3deg); }
        }
        .om-demo-inner {
          position: relative; z-index: 1;
          max-width: 1240px; margin: 0 auto; padding: 0 32px;
        }
        .om-demo-head {
          text-align: center; max-width: 760px; margin: 0 auto 56px;
        }
        .om-demo-eyebrow {
          display: inline-flex; align-items: center; gap: 8px;
          padding: 6px 14px 6px 10px; border-radius: 999px;
          background: rgba(20, 20, 27, 0.65);
          backdrop-filter: blur(12px);
          border: 1px solid rgba(124, 92, 255, 0.3);
          font-size: 12px; font-weight: 700; color: var(--accent);
          letter-spacing: 0.12em; text-transform: uppercase;
          margin-bottom: 22px;
          box-shadow: 0 8px 24px -6px rgba(124, 92, 255, 0.45);
        }
        .om-demo-eyebrow .pulse {
          width: 8px; height: 8px; border-radius: 50%;
          background: var(--green);
          box-shadow: 0 0 0 4px rgba(76, 217, 164, 0.20), 0 0 12px rgba(76, 217, 164, 0.55);
          animation: pulse-dot 1.4s ease-in-out infinite;
        }
        .om-demo-title {
          font-size: clamp(34px, 4.8vw, 62px);
          line-height: 1.0; letter-spacing: -0.04em;
          font-weight: 700; margin: 0 0 16px;
          color: var(--fg);
          text-wrap: balance;
        }
        .om-demo-title .grad {
          background: linear-gradient(105deg, #7c5cff 0%, #c66dff 35%, #ffffff 55%, #c66dff 75%, #7c5cff 100%);
          background-size: 280% 100%;
          -webkit-background-clip: text;
          background-clip: text;
          color: transparent;
          -webkit-text-fill-color: transparent;
          animation: shiny 5s linear infinite;
        }
        .om-demo-sub {
          font-size: 17px; line-height: 1.55; color: var(--muted);
          max-width: 58ch; margin: 0 auto;
        }
        @media (max-width: 880px) {
          .om-demo { padding: 40px 0 80px; }
        }
      `}),(0,r.jsxs)("div",{className:"om-demo-inner",children:[(0,r.jsxs)("div",{className:"om-demo-head",children:[(0,r.jsxs)("div",{className:"om-demo-eyebrow",children:[(0,r.jsx)("span",{className:"pulse"}),"live \xB7 \u0440\u0435\u0430\u043B\u044C\u043D\u044B\u0439 \u0430\u0433\u0435\u043D\u0442"]}),(0,r.jsxs)("h2",{className:"om-demo-title",children:["\u0421\u043C\u043E\u0442\u0440\u0438, \u043A\u0430\u043A ",(0,r.jsx)("span",{className:"grad",children:"\u0441\u043E\u0431\u0438\u0440\u0430\u0435\u0442\u0441\u044F \u0441\u0430\u0439\u0442"})," \u0437\u0430 \u043E\u0434\u0438\u043D \u0440\u0430\u0437\u0433\u043E\u0432\u043E\u0440."]}),(0,r.jsx)("p",{className:"om-demo-sub",children:"\u041F\u043E\u043B\u043D\u044B\u0439 \u0446\u0438\u043A\u043B: \u043F\u0440\u043E\u043C\u043F\u0442 \u2192 \u043A\u043E\u0434 \u2192 \u0432\u0435\u0440\u0441\u0438\u0438 \u2192 \u0434\u0435\u043F\u043B\u043E\u0439. \u0417\u0430\u043F\u0438\u0441\u044C \u0440\u0435\u0430\u043B\u044C\u043D\u043E\u0439 \u0441\u0435\u0441\u0441\u0438\u0438,\xA09\xA0\u044D\u0442\u0430\u043F\u043E\u0432,\xA026\xA0\u0441\u0435\u043A\u0443\u043D\u0434."})]}),(0,r.jsx)(sm,{from:.94,to:1,children:(0,r.jsx)(qf,{})})]})]})}var cd={visitka:"linear-gradient(135deg, #7c5cff 0%, #c66dff 100%)",shop:"linear-gradient(135deg, #5cb8ff 0%, #7c5cff 100%)",chat:"linear-gradient(135deg, #4cd9a4 0%, #5cb8ff 100%)",bot:"linear-gradient(135deg, #ec4cb8 0%, #7c5cff 100%)",saas:"linear-gradient(135deg, #ffd166 0%, #ff8a4d 100%)"},pm={visitka:"#7c5cff",shop:"#0ea5e9",chat:"#10b981",bot:"#c1399a",saas:"#e8741f"};function pd({kind:e,variant:t}){let n=pm[e]||"#7c5cff";return(0,r.jsxs)(r.Fragment,{children:[e==="visitka"&&t===1&&(0,r.jsx)(dm,{a:n}),e==="visitka"&&t===2&&(0,r.jsx)(um,{a:n}),e==="visitka"&&t===3&&(0,r.jsx)(fm,{a:n}),e==="shop"&&t===1&&(0,r.jsx)(mm,{a:n}),e==="shop"&&t===2&&(0,r.jsx)(gm,{a:n}),e==="shop"&&t===3&&(0,r.jsx)(vm,{a:n}),e==="chat"&&t===1&&(0,r.jsx)(xm,{a:n}),e==="chat"&&t===2&&(0,r.jsx)(hm,{a:n}),e==="chat"&&t===3&&(0,r.jsx)(bm,{a:n}),e==="bot"&&t===1&&(0,r.jsx)(ym,{a:n}),e==="bot"&&t===2&&(0,r.jsx)(wm,{a:n}),e==="bot"&&t===3&&(0,r.jsx)(km,{a:n}),e==="saas"&&t===1&&(0,r.jsx)(Nm,{a:n}),e==="saas"&&t===2&&(0,r.jsx)(zm,{a:n}),e==="saas"&&t===3&&(0,r.jsx)(Sm,{a:n})]})}var x=(e,t=500,n="#0f172a",a={})=>({fontSize:e,fontWeight:t,color:n,lineHeight:1.18,letterSpacing:-.012,fontFamily:"Onest, sans-serif",...a}),U=(e,t="#64748b")=>({fontSize:e,color:t,fontFamily:"JetBrains Mono, monospace",letterSpacing:.02,lineHeight:1.2,fontWeight:500});var ma=({children:e,bg:t,color:n,size:a=4.5})=>(0,r.jsx)("div",{style:{padding:"1px 5px",borderRadius:3,background:t,color:n,fontSize:a,fontWeight:700,letterSpacing:.04,display:"inline-block",whiteSpace:"nowrap",textTransform:"uppercase",lineHeight:1.2},children:e}),V=({d:e,size:t=8,color:n="currentColor",stroke:a=1.6,fill:i="none"})=>(0,r.jsx)("svg",{width:t,height:t,viewBox:"0 0 24 24",fill:i,stroke:n,strokeWidth:a,strokeLinecap:"round",strokeLinejoin:"round",style:{flexShrink:0,display:"block"},children:(0,r.jsx)("path",{d:e})}),$={search:"M16 16l-4-4M14 9a5 5 0 11-10 0 5 5 0 0110 0",cart:"M3 3h2l2 12h13M9 19a2 2 0 11-4 0 2 2 0 014 0M19 19a2 2 0 11-4 0 2 2 0 014 0",heart:"M20.84 4.61a5.5 5.5 0 00-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 00-7.78 7.78L12 21.23l8.84-8.84a5.5 5.5 0 000-7.78z",star:"M12 2l3 7 7 .6-5.3 4.6 1.6 7-6.3-3.8L5.7 21l1.6-7L2 9.6 9 9z",clock:"M12 22a10 10 0 100-20 10 10 0 000 20zM12 6v6l4 2",arrow:"M5 12h14M13 6l6 6-6 6",check:"M5 13l4 4 10-10",bell:"M18 8a6 6 0 10-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.73 21a2 2 0 01-3.46 0",pin:"M12 22s8-6 8-12a8 8 0 10-16 0c0 6 8 12 8 12zM12 11a3 3 0 100-6 3 3 0 000 6z",paper:"M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z",flag:"M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1zM4 22V15",zap:"M13 2L4 14h7l-1 8 9-12h-7l1-8z",shield:"M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"};function dm({a:e}){return(0,r.jsxs)("div",{className:"mc-page",style:{padding:"6px 7px",gap:4,position:"relative"},children:[(0,r.jsxs)("div",{style:{display:"flex",alignItems:"center",justifyContent:"space-between"},children:[(0,r.jsx)("span",{style:x(7,800,e),children:"\u2726 RAZOR"}),(0,r.jsxs)("div",{style:{display:"flex",alignItems:"center",gap:1.5},children:[(0,r.jsx)(V,{d:$.star,size:5,color:e,fill:e}),(0,r.jsx)("span",{style:x(4.5,700,"#0f172a"),children:"4.9"}),(0,r.jsx)("span",{style:x(4,500,"#94a3b8"),children:"\xB7 247"})]})]}),(0,r.jsxs)("div",{style:{flex:1,display:"flex",flexDirection:"column",justifyContent:"center",gap:1.5,paddingTop:3},children:[(0,r.jsx)("div",{style:x(11,900,"#0f172a"),children:"\u041C\u0443\u0436\u0441\u043A\u0430\u044F"}),(0,r.jsx)("div",{style:{...x(11,900),background:`linear-gradient(90deg, #0f172a, ${e})`,WebkitBackgroundClip:"text",WebkitTextFillColor:"transparent"},children:"\u0441\u0442\u0440\u0438\u0436\u043A\u0430"}),(0,r.jsxs)("div",{style:x(5,500,"#64748b",{marginTop:2,display:"flex",alignItems:"center",gap:2.5}),children:[(0,r.jsx)(V,{d:$.clock,size:5,color:"#64748b"}),"\u0431\u043B\u0438\u0436\u0430\u0439\u0448\u0435\u0435 \u043E\u043A\u043D\u043E ",(0,r.jsx)("b",{style:x(5,700,"#0f172a",{display:"inline"}),children:"\u0441\u0435\u0433\u043E\u0434\u043D\u044F 14:00"})]})]}),(0,r.jsxs)("div",{style:{display:"flex",alignItems:"center",gap:4,marginTop:2},children:[(0,r.jsxs)("div",{style:{display:"inline-flex",alignItems:"center",gap:3,padding:"3px 9px",borderRadius:999,background:e,color:"white",...x(5,700)},children:["\u0437\u0430\u043F\u0438\u0441\u0430\u0442\u044C\u0441\u044F ",(0,r.jsx)(V,{d:$.arrow,size:6,color:"white",stroke:2.2})]}),(0,r.jsx)("span",{style:x(5,500,"#94a3b8"),children:"\u043E\u0442 1 500 \u20BD"})]})]})}function um({a:e}){return(0,r.jsxs)("div",{className:"mc-page",style:{padding:4,flexDirection:"row",gap:4},children:[(0,r.jsxs)("div",{style:{flex:.9,background:`linear-gradient(160deg, ${e}99, ${e})`,borderRadius:5,position:"relative",overflow:"hidden"},children:[(0,r.jsx)("div",{style:{position:"absolute",top:2,left:2},children:(0,r.jsx)(ma,{bg:"rgba(255,255,255,0.95)",color:"#0f172a",size:4,children:"5 \u2605"})}),(0,r.jsx)("div",{style:{position:"absolute",top:0,right:0,width:20,height:12,background:"rgba(255,255,255,0.15)",borderBottomLeftRadius:8}}),(0,r.jsx)("div",{style:{position:"absolute",bottom:2,right:3,...x(4,600,"rgba(255,255,255,0.85)")},children:"4/12"})]}),(0,r.jsxs)("div",{style:{flex:1.15,display:"flex",flexDirection:"column",justifyContent:"center",gap:2},children:[(0,r.jsx)("div",{style:x(8,900,"#0f172a"),children:"RAZOR"}),(0,r.jsx)("div",{style:x(4.5,500,"#64748b"),children:"\u043F\u0440\u0435\u043C\u0438\u0443\u043C \u0431\u0430\u0440\u0431\u0435\u0440\u0448\u043E\u043F \xB7 \u0441 2018"}),(0,r.jsx)("div",{style:{display:"flex",flexDirection:"column",gap:1.5,marginTop:1.5},children:[{i:$.flag,t:"5 \u043C\u0430\u0441\u0442\u0435\u0440\u043E\u0432"},{i:$.clock,t:"24/7 \u043E\u043D\u043B\u0430\u0439\u043D"},{i:$.pin,t:"\u043C. \u0422\u0432\u0435\u0440\u0441\u043A\u0430\u044F"}].map((t,n)=>(0,r.jsxs)("div",{style:{display:"flex",alignItems:"center",gap:2.5},children:[(0,r.jsx)(V,{d:t.i,size:5,color:e}),(0,r.jsx)("span",{style:x(4.5,500,"#334155"),children:t.t})]},n))}),(0,r.jsx)("div",{style:{...x(5,700,"white"),padding:"2.5px 7px",borderRadius:999,background:e,alignSelf:"flex-start",marginTop:2},children:"\u0437\u0430\u043F\u0438\u0441\u0430\u0442\u044C\u0441\u044F"})]})]})}function fm({a:e}){let t=[{n:"\u0421\u0442\u0440\u0438\u0436\u043A\u0430",p:"1 500",d:"40 \u043C\u0438\u043D",on:!1},{n:"\u0411\u043E\u0440\u043E\u0434\u0430",p:"900",d:"20 \u043C\u0438\u043D",on:!0},{n:"\u0411\u0440\u0438\u0442\u044C\u0451",p:"1 200",d:"30 \u043C\u0438\u043D",on:!1}];return(0,r.jsxs)("div",{className:"mc-page",style:{padding:5,gap:3},children:[(0,r.jsxs)("div",{style:{display:"flex",alignItems:"center",justifyContent:"space-between"},children:[(0,r.jsx)("div",{style:x(7,800,"#0f172a"),children:"\u0423\u0441\u043B\u0443\u0433\u0438"}),(0,r.jsx)("div",{style:U(4.5),children:"3 \u0438\u0437 12"})]}),(0,r.jsx)("div",{style:{display:"grid",gridTemplateColumns:"1fr 1fr 1fr",gap:3,flex:1},children:t.map((n,a)=>(0,r.jsxs)("div",{style:{background:n.on?`linear-gradient(160deg, ${e}25, ${e}10)`:"#f8fafc",borderRadius:5,padding:3.5,display:"flex",flexDirection:"column",justifyContent:"space-between",border:n.on?`1px solid ${e}80`:"1px solid #e2e8f0",position:"relative"},children:[n.on&&(0,r.jsx)("div",{style:{position:"absolute",top:1.5,right:1.5},children:(0,r.jsx)(ma,{bg:e,color:"white",size:3.5,children:"\u0445\u0438\u0442"})}),(0,r.jsx)(V,{d:n.on?$.zap:$.shield,size:7,color:n.on?e:"#94a3b8"}),(0,r.jsxs)("div",{style:{display:"flex",flexDirection:"column",gap:.5},children:[(0,r.jsx)("div",{style:x(5,700,"#0f172a"),children:n.n}),(0,r.jsx)("div",{style:x(3.5,500,"#94a3b8"),children:n.d}),(0,r.jsxs)("div",{style:x(5,800,n.on?e:"#0f172a",{marginTop:1}),children:[n.p," \u20BD"]})]})]},a))}),(0,r.jsx)("div",{style:{...x(4.5,600,"#fff"),padding:"3px 8px",borderRadius:999,background:"#0f172a",alignSelf:"stretch",textAlign:"center"},children:"\u0437\u0430\u043F\u0438\u0441\u0430\u0442\u044C\u0441\u044F \xB7 \u043E\u0442 900 \u20BD"})]})}function mm({a:e}){let t=[{n:"\u0422\u043E\u043B\u0441\u0442\u043E\u0432\u043A\u0430",p:"2 990",old:"3 990",sale:!0},{n:"\u0428\u043E\u0440\u0442\u044B",p:"1 590",old:null,sale:!1},{n:"\u041A\u0443\u0440\u0442\u043A\u0430",p:"5 490",old:null,sale:!1},{n:"\u0411\u0440\u044E\u043A\u0438",p:"2 290",old:"2 890",sale:!0}],n=["#0f172a","#e8741f","#10b981","#7c5cff"];return(0,r.jsxs)("div",{className:"mc-page",style:{padding:5,gap:3},children:[(0,r.jsxs)("div",{style:{display:"flex",alignItems:"center",justifyContent:"space-between"},children:[(0,r.jsx)("span",{style:x(7,900,"#0f172a"),children:"POLE"}),(0,r.jsxs)("div",{style:{display:"flex",alignItems:"center",gap:4},children:[(0,r.jsx)(V,{d:$.search,size:7,color:"#64748b"}),(0,r.jsx)(V,{d:$.heart,size:7,color:"#64748b"}),(0,r.jsxs)("div",{style:{position:"relative"},children:[(0,r.jsx)(V,{d:$.cart,size:7,color:e}),(0,r.jsx)("span",{style:{position:"absolute",top:-3,right:-3,width:6,height:6,borderRadius:"50%",background:"#ef4444",color:"white",fontSize:4,fontWeight:800,display:"flex",alignItems:"center",justifyContent:"center",lineHeight:1},children:"3"})]})]})]}),(0,r.jsx)("div",{style:{display:"flex",gap:3,alignItems:"center"},children:["\u0432\u0441\u0451","\u0432\u0435\u0440\u0445","\u043D\u0438\u0437","\u043E\u0431\u0443\u0432\u044C"].map((a,i)=>(0,r.jsx)("span",{style:{...x(4,i===0?700:500,i===0?"#0f172a":"#64748b"),borderBottom:i===0?`1.5px solid ${e}`:"1.5px solid transparent",paddingBottom:1},children:a},a))}),(0,r.jsx)("div",{style:{display:"grid",gridTemplateColumns:"1fr 1fr",gap:2.5,flex:1},children:t.map((a,i)=>(0,r.jsxs)("div",{style:{background:`linear-gradient(135deg, ${n[i]}, ${n[i]}66)`,borderRadius:4,padding:2.5,display:"flex",flexDirection:"column",justifyContent:"space-between",position:"relative",overflow:"hidden"},children:[a.sale&&(0,r.jsx)("div",{style:{position:"absolute",top:1.5,left:1.5},children:(0,r.jsx)(ma,{bg:"#ef4444",color:"white",size:3.5,children:"\u221225%"})}),(0,r.jsx)("div",{style:{position:"absolute",top:1.5,right:1.5},children:(0,r.jsx)(V,{d:$.heart,size:5,color:"white",stroke:2})}),(0,r.jsxs)("div",{style:{display:"flex",flexDirection:"column",gap:.5,marginTop:"auto"},children:[(0,r.jsx)("div",{style:x(4,700,"white"),children:a.n}),(0,r.jsxs)("div",{style:{display:"flex",alignItems:"baseline",gap:2.5},children:[(0,r.jsxs)("span",{style:x(5,800,"white"),children:[a.p," \u20BD"]}),a.old&&(0,r.jsx)("span",{style:x(3.5,500,"rgba(255,255,255,0.65)",{textDecoration:"line-through"}),children:a.old})]})]})]},i))})]})}function gm({a:e}){return(0,r.jsxs)("div",{className:"mc-page",style:{padding:5,gap:3},children:[(0,r.jsxs)("div",{style:{height:"40%",background:`linear-gradient(135deg, #0f172a, ${e})`,borderRadius:5,position:"relative",overflow:"hidden"},children:[(0,r.jsx)("div",{style:{position:"absolute",top:2,left:2},children:(0,r.jsx)(ma,{bg:"white",color:"#0f172a",size:4,children:"NEW"})}),(0,r.jsx)("div",{style:{position:"absolute",top:2,right:2},children:(0,r.jsx)(V,{d:$.heart,size:6,color:"white",stroke:2})}),(0,r.jsx)("div",{style:{position:"absolute",bottom:2,right:2.5,...U(3.5,"rgba(255,255,255,0.85)")},children:"1/4"}),(0,r.jsx)("div",{style:{position:"absolute",bottom:2,left:3,display:"flex",gap:2},children:[0,1,2,3].map(t=>(0,r.jsx)("span",{style:{width:3,height:3,borderRadius:"50%",background:t===0?"white":"rgba(255,255,255,0.4)"}},t))})]}),(0,r.jsxs)("div",{children:[(0,r.jsxs)("div",{style:{display:"flex",alignItems:"center",gap:3,marginBottom:1},children:[(0,r.jsx)("span",{style:x(4,500,"#94a3b8"),children:"\u0421\u0432\u0438\u0442\u0435\u0440\u0430"}),(0,r.jsx)(V,{d:$.star,size:3.5,color:"#fbbf24",fill:"#fbbf24"}),(0,r.jsx)("span",{style:x(3.5,600,"#64748b"),children:"4.8 \xB7 124"})]}),(0,r.jsx)("div",{style:x(7,900,"#0f172a"),children:"\u0421\u0432\u0438\u0442\u0435\u0440 \xABPolo Oversize\xBB"}),(0,r.jsx)("div",{style:x(4.5,500,"#64748b",{marginTop:1}),children:"100% \u0445\u043B\u043E\u043F\u043E\u043A \xB7 3 \u0446\u0432\u0435\u0442\u0430"})]}),(0,r.jsxs)("div",{style:{display:"flex",alignItems:"center",gap:2},children:[["#0f172a","#fbbf24","#10b981"].map((t,n)=>(0,r.jsx)("span",{style:{width:7,height:7,borderRadius:"50%",background:t,border:n===0?`1.5px solid ${e}`:"1px solid #e2e8f0",boxShadow:n===0?"0 0 0 1px white":"none"}},n)),(0,r.jsx)("span",{style:{marginLeft:"auto",...x(4.5,600,"#64748b")},children:"S \xB7 M \xB7 L"})]}),(0,r.jsxs)("div",{style:{display:"flex",alignItems:"center",justifyContent:"space-between",marginTop:"auto"},children:[(0,r.jsxs)("div",{children:[(0,r.jsx)("div",{style:x(8,900,"#0f172a"),children:"3 290 \u20BD"}),(0,r.jsx)("div",{style:x(3.5,500,"#94a3b8"),children:"\u0421\u0411\u041F \xB7 \u043A\u0430\u0440\u0442\u0430 \xB7 \u0432 \u0440\u0430\u0441\u0441\u0440\u043E\u0447\u043A\u0443"})]}),(0,r.jsxs)("div",{style:{...x(5,700,"white"),padding:"3.5px 9px",borderRadius:999,background:"#0f172a",display:"flex",alignItems:"center",gap:3},children:[(0,r.jsx)(V,{d:$.cart,size:6,color:"white",stroke:2}),"\u0432 \u043A\u043E\u0440\u0437\u0438\u043D\u0443"]})]})]})}function vm({a:e}){let t=[{n:"\u0412\u0441\u0435 \u0442\u043E\u0432\u0430\u0440\u044B",c:248,on:!0},{n:"\u0412\u0435\u0440\u0445",c:86,on:!1},{n:"\u041D\u0438\u0437",c:62,on:!1},{n:"\u041E\u0431\u0443\u0432\u044C",c:44,on:!1},{n:"\u0410\u043A\u0441\u0435\u0441\u0441\u0443\u0430\u0440\u044B",c:56,on:!1}],n=[{n:"\u0425\u0443\u0434\u0438 basic",p:"2 490",c:"#0f172a"},{n:"\u0414\u0436\u0438\u043D\u0441\u044B slim",p:"3 890",c:"#7c5cff"},{n:"\u041A\u0435\u0434\u044B low",p:"4 290",c:"#10b981"}];return(0,r.jsxs)("div",{className:"mc-page",style:{padding:4,flexDirection:"row",gap:4},children:[(0,r.jsxs)("div",{style:{width:"36%",display:"flex",flexDirection:"column",gap:1.5},children:[(0,r.jsx)("div",{style:x(4.5,800,"#94a3b8",{marginBottom:2}),children:"\u043A\u0430\u0442\u0430\u043B\u043E\u0433"}),t.map((a,i)=>(0,r.jsxs)("div",{style:{display:"flex",alignItems:"center",justifyContent:"space-between",padding:"2px 4px",borderRadius:3,background:a.on?`${e}15`:"transparent",borderLeft:a.on?`1.5px solid ${e}`:"1.5px solid transparent"},children:[(0,r.jsx)("span",{style:x(4.5,a.on?700:500,a.on?e:"#475569"),children:a.n}),(0,r.jsx)("span",{style:x(3.5,600,a.on?e:"#94a3b8"),children:a.c})]},a.n))]}),(0,r.jsxs)("div",{style:{flex:1,display:"flex",flexDirection:"column",gap:3,paddingTop:2},children:[(0,r.jsxs)("div",{style:{display:"flex",alignItems:"center",justifyContent:"space-between"},children:[(0,r.jsx)("div",{style:x(5.5,800,"#0f172a"),children:"\u0425\u0438\u0442\u044B \u043D\u0435\u0434\u0435\u043B\u0438"}),(0,r.jsx)("span",{style:U(3.5),children:"\u2191\u2193"})]}),n.map((a,i)=>(0,r.jsxs)("div",{style:{display:"flex",alignItems:"center",gap:3},children:[(0,r.jsx)("div",{style:{width:13,height:13,borderRadius:3,background:`linear-gradient(135deg, ${a.c}, ${a.c}80)`,flexShrink:0,position:"relative"},children:(0,r.jsx)("div",{style:{position:"absolute",top:-1,right:-1,width:4,height:4,borderRadius:"50%",background:"#10b981",border:"0.5px solid white"}})}),(0,r.jsxs)("div",{style:{flex:1,minWidth:0},children:[(0,r.jsx)("div",{style:x(4.5,700,"#0f172a"),children:a.n}),(0,r.jsxs)("div",{style:{display:"flex",alignItems:"baseline",gap:2},children:[(0,r.jsxs)("span",{style:x(4,700,e),children:[a.p," \u20BD"]}),(0,r.jsx)("span",{style:x(3,500,"#94a3b8"),children:"\u0432 \u043D\u0430\u043B\u0438\u0447\u0438\u0438"})]})]}),(0,r.jsx)(V,{d:$.cart,size:6,color:e})]},i))]})]})}function xm({a:e}){let t=[{n:"11-\u0410 \xB7 \u043E\u0431\u0449\u0438\u0439",u:3,on:!0},{n:"11-\u0411 \xB7 \u043E\u0431\u0449\u0438\u0439",u:0,on:!1},{n:"\u0443\u0447\u0438\u0442\u0435\u043B\u044F",u:1,on:!1},{n:"\u043E\u0431\u044A\u044F\u0432\u043B\u0435\u043D\u0438\u044F",u:0,on:!1}];return(0,r.jsxs)("div",{className:"mc-page",style:{padding:0,flexDirection:"row"},children:[(0,r.jsxs)("div",{style:{width:"40%",background:"#f1f5f9",display:"flex",flexDirection:"column",gap:1.5,padding:4,borderRight:"1px solid #e2e8f0"},children:[(0,r.jsxs)("div",{style:{display:"flex",alignItems:"center",gap:2.5,marginBottom:2},children:[(0,r.jsx)("div",{style:{width:9,height:9,borderRadius:2.5,background:`linear-gradient(135deg, ${e}, #5cb8ff)`,display:"flex",alignItems:"center",justifyContent:"center",...x(5,900,"white")},children:"\u041B"}),(0,r.jsx)("span",{style:x(5,800,"#0f172a"),children:"\u041B\u0438\u0446\u0435\u0439 \u21167"})]}),(0,r.jsx)("div",{style:U(3.5,"#94a3b8"),children:"\u043A\u0430\u043D\u0430\u043B\u044B \xB7 4"}),t.map((n,a)=>(0,r.jsxs)("div",{style:{display:"flex",alignItems:"center",gap:2,padding:"1.5px 4px",borderRadius:3,background:n.on?e+"22":"transparent"},children:[(0,r.jsx)("span",{style:x(3.5,600,n.on?e:"#94a3b8"),children:"#"}),(0,r.jsx)("span",{style:{...x(4.5,n.on?700:500,n.on?"#0f172a":"#64748b"),flex:1},children:n.n}),n.u>0&&(0,r.jsx)("span",{style:{padding:"0.5px 3.5px",borderRadius:999,background:n.on?e:"#94a3b8",color:"white",fontSize:3.5,fontWeight:800,lineHeight:1.2},children:n.u})]},a))]}),(0,r.jsxs)("div",{style:{flex:1,padding:4,display:"flex",flexDirection:"column",gap:0,justifyContent:"flex-end"},children:[(0,r.jsxs)("div",{style:{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:2.5},children:[(0,r.jsx)("span",{style:x(4,700,"#0f172a"),children:"# 11-\u0410"}),(0,r.jsx)("span",{style:U(3.5),children:"12 \u043E\u043D\u043B\u0430\u0439\u043D"})]}),(0,r.jsxs)("div",{style:{display:"flex",flexDirection:"column",gap:2.5,flex:1,justifyContent:"flex-end"},children:[(0,r.jsxs)("div",{children:[(0,r.jsx)("div",{style:U(3,"#94a3b8"),children:"\u0410\u043D\u043D\u0430 \xB7 10:24"}),(0,r.jsx)("div",{style:{padding:"2.5px 5px",background:"#f1f5f9",borderRadius:"6px 6px 6px 1px",alignSelf:"flex-start",maxWidth:"90%",...x(4,500,"#1e293b"),marginTop:.5},children:"\u0414\u0417 \u043D\u0430 \u0437\u0430\u0432\u0442\u0440\u0430 \u2014 \u0441\u0442\u0440.\xA042"})]}),(0,r.jsxs)("div",{style:{alignSelf:"flex-end",maxWidth:"70%"},children:[(0,r.jsx)("div",{style:{padding:"2.5px 5px",background:e,borderRadius:"6px 6px 1px 6px",...x(4,500,"white")},children:"\u043F\u043E\u043D\u044F\u043B, \u0441\u043F\u0430\u0441\u0438\u0431\u043E!"}),(0,r.jsx)("div",{style:{...U(3,"#94a3b8"),textAlign:"right",marginTop:.5},children:"10:25 \u2713\u2713"})]})]})]})]})}function hm({a:e}){return(0,r.jsxs)("div",{className:"mc-page",style:{padding:0,flexDirection:"column"},children:[(0,r.jsxs)("div",{style:{padding:"4px 5px",background:"#f1f5f9",display:"flex",alignItems:"center",gap:3,borderBottom:"1px solid #e2e8f0"},children:[(0,r.jsxs)("div",{style:{width:10,height:10,borderRadius:"50%",background:`linear-gradient(135deg, ${e}, ${e}99)`,display:"flex",alignItems:"center",justifyContent:"center",...x(5,900,"white"),position:"relative"},children:["\u0410",(0,r.jsx)("span",{style:{position:"absolute",bottom:-.5,right:-.5,width:4,height:4,borderRadius:"50%",background:"#10b981",border:"1px solid white"}})]}),(0,r.jsxs)("div",{style:{flex:1},children:[(0,r.jsx)("div",{style:x(5,800,"#0f172a"),children:"\u0410\u043D\u043D\u0430 \u0418\u0432\u0430\u043D\u043E\u0432\u0430"}),(0,r.jsx)("div",{style:x(3.5,500,"#10b981"),children:"\u25CF \u0432 \u0441\u0435\u0442\u0438 \xB7 \u043F\u0435\u0447\u0430\u0442\u0430\u0435\u0442\u2026"})]}),(0,r.jsx)(V,{d:$.bell,size:6,color:"#94a3b8"})]}),(0,r.jsxs)("div",{style:{flex:1,padding:4,display:"flex",flexDirection:"column",gap:3,justifyContent:"flex-end"},children:[(0,r.jsx)("div",{style:{alignSelf:"center",...U(3,"#94a3b8"),padding:"0.5px 5px",background:"#f1f5f9",borderRadius:999},children:"\u0441\u0435\u0433\u043E\u0434\u043D\u044F"}),(0,r.jsxs)("div",{style:{padding:"3px 6px",background:"#f1f5f9",borderRadius:"8px 8px 8px 2px",alignSelf:"flex-start",maxWidth:"78%",...x(4.5,500,"#1e293b")},children:["\u041A\u043E\u0433\u0434\u0430 \u043A\u043E\u043D\u0442\u0440\u043E\u043B\u044C\u043D\u0430\u044F?",(0,r.jsx)("div",{style:{...U(3,"#94a3b8"),marginTop:1},children:"10:22"})]}),(0,r.jsxs)("div",{style:{padding:"3px 6px",background:e,borderRadius:"8px 8px 2px 8px",alignSelf:"flex-end",maxWidth:"65%",...x(4.5,500,"white")},children:["\u0432 \u043F\u044F\u0442\u043D\u0438\u0446\u0443, 10:00",(0,r.jsx)("div",{style:{...x(3,500,"rgba(255,255,255,0.7)"),marginTop:1,textAlign:"right"},children:"10:23 \u2713\u2713"})]}),(0,r.jsx)("div",{style:{padding:"3px 6px",background:"#f1f5f9",borderRadius:"8px 8px 8px 2px",alignSelf:"flex-start",maxWidth:"50%",...x(4.5,500,"#1e293b")},children:"\u0441\u0443\u043F\u0435\u0440! \u{1F44D}"})]}),(0,r.jsxs)("div",{style:{padding:4,background:"#f1f5f9",display:"flex",alignItems:"center",gap:3,borderTop:"1px solid #e2e8f0"},children:[(0,r.jsx)(V,{d:$.paper,size:6,color:"#94a3b8"}),(0,r.jsx)("span",{style:x(4,500,"#94a3b8",{flex:1}),children:"\u0441\u043E\u043E\u0431\u0449\u0435\u043D\u0438\u0435\u2026"}),(0,r.jsx)("div",{style:{width:10,height:10,borderRadius:2,background:e,display:"flex",alignItems:"center",justifyContent:"center"},children:(0,r.jsx)(V,{d:$.arrow,size:5,color:"white",stroke:2.4})})]})]})}function bm({a:e}){let t=[{n:"\u0410\u043D\u044F",c:"#7c5cff",on:!0},{n:"\u041C\u0430\u043A\u0441",c:"#ec4cb8",on:!0},{n:"\u041B\u0435\u0432",c:"#5cb8ff",on:!1},{n:"\u0421\u043E\u043D\u044F",c:"#10b981",on:!0},{n:"\u0414\u0438\u043C\u0430",c:"#fbbf24",on:!1}];return(0,r.jsxs)("div",{className:"mc-page",style:{padding:0,flexDirection:"row"},children:[(0,r.jsxs)("div",{style:{flex:1,padding:4,display:"flex",flexDirection:"column",gap:0,justifyContent:"flex-end"},children:[(0,r.jsxs)("div",{style:{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:2},children:[(0,r.jsx)("span",{style:x(4.5,700,"#0f172a"),children:"\u0413\u0440\u0443\u043F\u043F\u0430 \xB7 \u043F\u043E\u0445\u043E\u0434 \u0432 \u043A\u0438\u043D\u043E"}),(0,r.jsx)("span",{style:U(3.5,"#10b981"),children:"5 \u043E\u043D\u043B\u0430\u0439\u043D"})]}),(0,r.jsxs)("div",{style:{display:"flex",flexDirection:"column",gap:2.5,flex:1,justifyContent:"flex-end"},children:[(0,r.jsxs)("div",{style:{alignSelf:"flex-start",maxWidth:"85%"},children:[(0,r.jsx)("div",{style:U(3,"#94a3b8"),children:"\u0410\u043D\u044F \xB7 18:42"}),(0,r.jsxs)("div",{style:{padding:"2.5px 5px",background:"#f1f5f9",borderRadius:6,...x(4.5,500,"#1e293b"),marginTop:.5},children:[(0,r.jsx)("span",{style:{color:"#7c5cff",fontWeight:700},children:"\u0410\u043D\u044F:"})," \u043A\u0442\u043E \u0438\u0434\u0451\u0442 \u0432 \u043A\u0438\u043D\u043E?"]})]}),(0,r.jsx)("div",{style:{padding:"2.5px 5px",background:e,borderRadius:6,alignSelf:"flex-end",maxWidth:"58%",...x(4.5,500,"white")},children:"\u044F \u0432 \u0434\u0435\u043B\u0435! \u{1F37F}"}),(0,r.jsxs)("div",{style:{padding:"2.5px 5px",background:"#f1f5f9",borderRadius:6,alignSelf:"flex-start",maxWidth:"66%",...x(4.5,500,"#1e293b")},children:[(0,r.jsx)("span",{style:{color:"#5cb8ff",fontWeight:700},children:"\u041B\u0435\u0432:"})," + \u043E\u0434\u043E\u0431\u0440\u044F\u044E"]})]})]}),(0,r.jsxs)("div",{style:{width:"32%",background:"#f1f5f9",padding:4,display:"flex",flexDirection:"column",gap:3,borderLeft:"1px solid #e2e8f0"},children:[(0,r.jsx)("div",{style:x(4,800,"#94a3b8",{textTransform:"uppercase",letterSpacing:.08}),children:"\u0443\u0447\u0430\u0441\u0442\u043D\u0438\u043A\u0438 \xB7 5"}),t.map((n,a)=>(0,r.jsxs)("div",{style:{display:"flex",alignItems:"center",gap:2.5},children:[(0,r.jsxs)("div",{style:{position:"relative",width:7,height:7,borderRadius:"50%",background:`linear-gradient(135deg, ${n.c}, ${n.c}aa)`,display:"flex",alignItems:"center",justifyContent:"center",...x(3.5,800,"white"),flexShrink:0},children:[n.n[0],n.on&&(0,r.jsx)("span",{style:{position:"absolute",bottom:-.5,right:-.5,width:2.5,height:2.5,borderRadius:"50%",background:"#10b981",border:"0.5px solid #f1f5f9"}})]}),(0,r.jsx)("span",{style:x(4.5,500,"#0f172a"),children:n.n})]},a))]})]})}function ym({a:e}){return(0,r.jsxs)("div",{className:"mc-page",style:{padding:0,background:"#0e1424"},children:[(0,r.jsxs)("div",{style:{padding:"4px 5px",display:"flex",alignItems:"center",gap:3,borderBottom:"1px solid #1e293b"},children:[(0,r.jsxs)("div",{style:{width:11,height:11,borderRadius:"50%",background:`linear-gradient(135deg, ${e}, #7c5cff)`,display:"flex",alignItems:"center",justifyContent:"center",...x(6,900,"white"),position:"relative"},children:["\u2299",(0,r.jsx)("span",{style:{position:"absolute",bottom:-.5,right:-.5,width:4,height:4,borderRadius:"50%",background:"#10b981",border:"1px solid #0e1424"}})]}),(0,r.jsxs)("div",{style:{flex:1,minWidth:0},children:[(0,r.jsxs)("div",{style:{display:"flex",alignItems:"center",gap:2},children:[(0,r.jsx)("span",{style:x(5,800,"white"),children:"knowledge_bot"}),(0,r.jsx)(ma,{bg:`${e}30`,color:e,size:3.5,children:"AI"})]}),(0,r.jsx)("div",{style:x(3.5,500,"#94a3b8"),children:"1 247 \u043E\u0442\u0432\u0435\u0442\u043E\u0432 \xB7 \u0442\u043E\u0447\u043D\u043E\u0441\u0442\u044C 94%"})]}),(0,r.jsx)(V,{d:$.bell,size:6,color:"#64748b"})]}),(0,r.jsxs)("div",{style:{flex:1,padding:4,display:"flex",flexDirection:"column",gap:2.5,justifyContent:"flex-end"},children:[(0,r.jsx)("div",{style:{padding:"3px 6px",background:e,borderRadius:"8px 8px 2px 8px",alignSelf:"flex-end",maxWidth:"70%",...x(4.5,500,"white")},children:"\u0427\u0442\u043E \u0442\u0430\u043A\u043E\u0435 RAG?"}),(0,r.jsxs)("div",{style:{alignSelf:"flex-start",maxWidth:"88%"},children:[(0,r.jsxs)("div",{style:{padding:"3px 6px",background:"#1e293b",borderRadius:"8px 8px 8px 2px",...x(4,500,"rgba(255,255,255,0.92)")},children:[(0,r.jsx)("b",{style:{color:e,fontWeight:700},children:"Retrieval Augmented Generation"})," \u2014 \u043C\u043E\u0434\u0435\u043B\u044C \u0438\u0449\u0435\u0442 \u0432 \u0432\u0430\u0448\u0435\u0439 \u0431\u0430\u0437\u0435 \u0438 \u043E\u0442\u0432\u0435\u0447\u0430\u0435\u0442 \u043F\u043E \u043D\u0435\u0439."]}),(0,r.jsxs)("div",{style:{display:"flex",alignItems:"center",gap:2.5,marginTop:2,padding:"1.5px 5px",background:"rgba(124,92,255,0.1)",borderRadius:4,alignSelf:"flex-start"},children:[(0,r.jsx)(V,{d:$.paper,size:4.5,color:e}),(0,r.jsx)("span",{style:x(3.5,600,e),children:"rag-guide.pdf \xB7 \u0441\u0442\u0440. 4"})]})]})]}),(0,r.jsxs)("div",{style:{padding:4,display:"flex",alignItems:"center",gap:3,borderTop:"1px solid #1e293b"},children:[(0,r.jsx)("span",{style:x(4,500,"#475569",{flex:1}),children:"\u0441\u043F\u0440\u043E\u0441\u0438\u0442\u0435 \u0431\u043E\u0442\u0430\u2026"}),(0,r.jsx)("div",{style:{width:10,height:10,borderRadius:2,background:e,display:"flex",alignItems:"center",justifyContent:"center"},children:(0,r.jsx)(V,{d:$.arrow,size:5,color:"white",stroke:2.4})})]})]})}function wm({a:e}){let t=[{v:"1 247",l:"\u043E\u0442\u0432\u0435\u0442\u043E\u0432 \xB7 \u0441\u0435\u0433\u043E\u0434\u043D\u044F",c:e,t:"+18%"},{v:"94%",l:"\u0442\u043E\u0447\u043D\u043E\u0441\u0442\u044C",c:"#10b981",t:"+2%"},{v:"32ms",l:"\u043C\u0435\u0434\u0438\u0430\u043D\u0430 \u043E\u0442\u0432.",c:"#5cb8ff",t:"\u22125ms"}];return(0,r.jsxs)("div",{className:"mc-page",style:{padding:5,gap:3},children:[(0,r.jsxs)("div",{style:{display:"flex",alignItems:"center",justifyContent:"space-between"},children:[(0,r.jsxs)("div",{children:[(0,r.jsx)("div",{style:x(6,800,"#0f172a"),children:"\u0410\u043D\u0430\u043B\u0438\u0442\u0438\u043A\u0430"}),(0,r.jsx)("div",{style:U(3.5,"#94a3b8"),children:"knowledge_bot \xB7 24\u0447"})]}),(0,r.jsxs)("div",{style:{...x(4,700,"white"),padding:"2px 6px",borderRadius:999,background:"#10b981",display:"flex",alignItems:"center",gap:2},children:[(0,r.jsx)("span",{style:{width:3,height:3,borderRadius:"50%",background:"white"}}),"live"]})]}),(0,r.jsx)("div",{style:{display:"grid",gridTemplateColumns:"1fr 1fr 1fr",gap:2.5},children:t.map((n,a)=>(0,r.jsxs)("div",{style:{background:"#f8fafc",border:"1px solid #e2e8f0",borderRadius:4,padding:"3px 4px",display:"flex",flexDirection:"column",gap:.5},children:[(0,r.jsx)("span",{style:x(7,900,n.c),children:n.v}),(0,r.jsx)("span",{style:x(3.5,500,"#64748b"),children:n.l}),(0,r.jsx)("span",{style:x(3.5,700,"#10b981"),children:n.t})]},a))}),(0,r.jsx)("div",{style:{flex:1,position:"relative",display:"flex",alignItems:"flex-end",gap:1.5,padding:"4px 0 2px",borderTop:"1px solid #f1f5f9"},children:[18,26,22,32,24,38,30,42,36,28].map((n,a)=>(0,r.jsx)("div",{style:{flex:1,height:`${n*1.6}%`,background:a===7?e:`${e}cc`,borderRadius:"2px 2px 0 0",opacity:.4+a/18,position:"relative"},children:a===7&&(0,r.jsx)("span",{style:{position:"absolute",top:-6,left:"50%",transform:"translateX(-50%)",...x(3.5,700,e),whiteSpace:"nowrap"},children:"\u043F\u0438\u043A"})},a))}),(0,r.jsxs)("div",{style:{...U(3.5,"#94a3b8"),display:"flex",justifyContent:"space-between"},children:[(0,r.jsx)("span",{children:"0:00"}),(0,r.jsx)("span",{children:"\u043F\u043D \xB7 18:00"}),(0,r.jsx)("span",{children:"23:59"})]})]})}function km({a:e}){let t=[{c:"#10b981",s:"200",n:"POST /webhook \xB7 processed",t:"2s"},{c:"#10b981",s:"200",n:"POST /rag/search \xB7 4 hits",t:"4s"},{c:"#5cb8ff",s:"101",n:"WS /session \xB7 started",t:"7s"},{c:"#fbbf24",s:"429",n:"rate-limit \xB7 queued",t:"11s"},{c:"#10b981",s:"200",n:"POST /webhook \xB7 processed",t:"14s"}];return(0,r.jsxs)("div",{className:"mc-page",style:{padding:5,background:"#0e1424",gap:2.5},children:[(0,r.jsxs)("div",{style:{display:"flex",alignItems:"center",gap:3},children:[(0,r.jsx)("div",{style:{width:7,height:7,borderRadius:"50%",background:"#10b981",boxShadow:"0 0 6px #10b981"}}),(0,r.jsx)("span",{style:x(5,800,"white"),children:"Live logs"}),(0,r.jsx)("span",{style:{marginLeft:"auto",...U(3.5,"#475569")},children:"last 30s"})]}),(0,r.jsx)("div",{style:{flex:1,display:"flex",flexDirection:"column",gap:2,paddingTop:1},children:t.map((n,a)=>(0,r.jsxs)("div",{style:{display:"flex",alignItems:"center",gap:3},children:[(0,r.jsx)("span",{style:{padding:"0.5px 3px",borderRadius:2,background:`${n.c}22`,color:n.c,...U(3.5)},children:n.s}),(0,r.jsx)("span",{style:U(3.5,"rgba(255,255,255,0.65)",{flex:1,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}),children:n.n}),(0,r.jsx)("span",{style:U(3,"rgba(255,255,255,0.3)"),children:n.t})]},a))}),(0,r.jsxs)("div",{style:{display:"flex",alignItems:"center",gap:3,padding:"2px 5px",background:`${e}15`,borderRadius:3,border:`1px solid ${e}40`},children:[(0,r.jsx)(V,{d:$.zap,size:5,color:e}),(0,r.jsx)("span",{style:U(3.5,e),children:"throughput \xB7 42 req/s \xB7 p95 38ms"})]})]})}function Nm({a:e}){return(0,r.jsxs)("div",{className:"mc-page",style:{padding:0,flexDirection:"row"},children:[(0,r.jsxs)("div",{style:{width:"18%",background:"#f8fafc",display:"flex",flexDirection:"column",gap:4,padding:4,alignItems:"center",borderRight:"1px solid #e2e8f0"},children:[(0,r.jsx)("div",{style:{width:10,height:10,borderRadius:2.5,background:`linear-gradient(135deg, ${e}, #ff8a4d)`,...x(5,900,"white"),display:"flex",alignItems:"center",justifyContent:"center"},children:"O"}),[{g:"\u25E7",on:!0},{g:"\u25AD",on:!1},{g:"\u25CC",on:!1},{g:"\u2699",on:!1}].map((t,n)=>(0,r.jsx)("div",{style:{width:9,height:9,borderRadius:2,background:t.on?e:"transparent",display:"flex",alignItems:"center",justifyContent:"center",...x(5,700,t.on?"white":"#94a3b8"),border:t.on?"none":"1px solid transparent"},children:t.g},n))]}),(0,r.jsxs)("div",{style:{flex:1,padding:4,display:"flex",flexDirection:"column",gap:3},children:[(0,r.jsxs)("div",{style:{display:"flex",alignItems:"center",justifyContent:"space-between"},children:[(0,r.jsxs)("div",{children:[(0,r.jsx)("div",{style:x(5.5,800,"#0f172a"),children:"\u041F\u0440\u0438\u0432\u0435\u0442, \u0410\u0440\u0442\u0451\u043C!"}),(0,r.jsx)("div",{style:U(3.5),children:"\u043F\u043E\u043D\u0435\u0434\u0435\u043B\u044C\u043D\u0438\u043A, 24 \u043C\u0430\u044F"})]}),(0,r.jsx)(V,{d:$.bell,size:7,color:"#94a3b8"})]}),(0,r.jsxs)("div",{style:{display:"grid",gridTemplateColumns:"1fr 1fr",gap:2.5},children:[(0,r.jsxs)("div",{style:{background:"#f8fafc",border:"1px solid #e2e8f0",borderRadius:4,padding:"3px 4px"},children:[(0,r.jsx)("div",{style:U(3,"#94a3b8"),children:"\u0432\u044B\u0440\u0443\u0447\u043A\u0430"}),(0,r.jsx)("div",{style:x(7,900,"#0f172a"),children:"\u20BD284k"}),(0,r.jsx)("div",{style:x(3.5,700,"#10b981"),children:"+18% \u043A \u043F\u0440\u043E\u0448\u043B."})]}),(0,r.jsxs)("div",{style:{background:`${e}15`,borderRadius:4,padding:"3px 4px",border:`1px solid ${e}40`},children:[(0,r.jsx)("div",{style:U(3,"#94a3b8"),children:"\u043D\u043E\u0432\u044B\u0435 \u043F\u043E\u043B\u044C\u0437."}),(0,r.jsx)("div",{style:x(7,900,e),children:"+247"}),(0,r.jsx)("div",{style:x(3.5,700,"#10b981"),children:"+32% \xB7 WoW"})]})]}),(0,r.jsx)("div",{style:{flex:1,position:"relative",display:"flex",alignItems:"flex-end",gap:1.5,padding:"2px 0"},children:[12,18,22,16,28,24,32,26,38].map((t,n)=>(0,r.jsx)("div",{style:{flex:1,height:`${t*1.5}%`,background:`linear-gradient(180deg, ${e}, ${e}80)`,borderRadius:"2px 2px 0 0",opacity:.4+n/16}},n))})]})]})}function zm({a:e}){let t=[{n:"\u041F\u0440\u043E\u0444\u0438\u043B\u044C",i:"\u{1F464}",on:!0},{n:"\u041A\u043E\u043C\u0430\u043D\u0434\u0430",i:"\u{1F465}",on:!1},{n:"\u0411\u0438\u043B\u043B\u0438\u043D\u0433",i:"\u{1F4B3}",on:!1},{n:"API \u043A\u043B\u044E\u0447\u0438",i:"\u{1F511}",on:!1}];return(0,r.jsxs)("div",{className:"mc-page",style:{padding:0,flexDirection:"row"},children:[(0,r.jsxs)("div",{style:{width:"36%",background:"#f8fafc",padding:4,display:"flex",flexDirection:"column",gap:3,borderRight:"1px solid #e2e8f0"},children:[(0,r.jsxs)("div",{style:{display:"flex",alignItems:"center",gap:2.5},children:[(0,r.jsx)("div",{style:{width:10,height:10,borderRadius:"50%",background:`linear-gradient(135deg, ${e}, #ff8a4d)`,display:"flex",alignItems:"center",justifyContent:"center",...x(5,900,"white")},children:"\u0410"}),(0,r.jsxs)("div",{children:[(0,r.jsx)("div",{style:x(4.5,700,"#0f172a"),children:"\u0410\u0440\u0442\u0451\u043C \u041B."}),(0,r.jsx)("div",{style:U(3,"#94a3b8"),children:"PRO"})]})]}),(0,r.jsx)("div",{style:{height:1,background:"#e2e8f0"}}),t.map((n,a)=>(0,r.jsxs)("div",{style:{display:"flex",alignItems:"center",gap:2.5,padding:"2px 4px",borderRadius:3,background:n.on?`${e}22`:"transparent",borderLeft:n.on?`1.5px solid ${e}`:"1.5px solid transparent"},children:[(0,r.jsx)("span",{style:{fontSize:5},children:n.i}),(0,r.jsx)("span",{style:x(4.5,n.on?700:500,n.on?e:"#475569"),children:n.n})]},n.n))]}),(0,r.jsxs)("div",{style:{flex:1,padding:5,display:"flex",flexDirection:"column",gap:3},children:[(0,r.jsxs)("div",{children:[(0,r.jsx)("div",{style:x(6,800,"#0f172a"),children:"\u041F\u0440\u043E\u0444\u0438\u043B\u044C"}),(0,r.jsx)("div",{style:U(3.5),children:"\u043E\u0441\u043D\u043E\u0432\u043D\u0430\u044F \u0438\u043D\u0444\u043E\u0440\u043C\u0430\u0446\u0438\u044F"})]}),[{l:"\u0418\u043C\u044F",v:"\u0410\u0440\u0442\u0451\u043C"},{l:"Email",v:"artem@omnia.ru"},{l:"\u041A\u043E\u043C\u043F\u0430\u043D\u0438\u044F",v:"Omnia Labs"}].map((n,a)=>(0,r.jsxs)("div",{style:{display:"flex",flexDirection:"column",gap:1},children:[(0,r.jsx)("span",{style:x(3.5,700,"#94a3b8",{textTransform:"uppercase",letterSpacing:.05}),children:n.l}),(0,r.jsx)("div",{style:{padding:"2.5px 5px",background:"white",borderRadius:3,border:"1px solid #e2e8f0",...x(4.5,500,"#0f172a")},children:n.v})]},n.l)),(0,r.jsxs)("div",{style:{marginTop:"auto",alignSelf:"flex-end",display:"flex",gap:3},children:[(0,r.jsx)("div",{style:{...x(4.5,600,"#475569"),padding:"2.5px 7px",borderRadius:999,background:"#f1f5f9"},children:"\u043E\u0442\u043C\u0435\u043D\u0430"}),(0,r.jsxs)("div",{style:{...x(4.5,700,"white"),padding:"2.5px 9px",borderRadius:999,background:e,display:"flex",alignItems:"center",gap:2.5},children:[(0,r.jsx)(V,{d:$.check,size:5,color:"white",stroke:2.4}),"\u0441\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C"]})]})]})]})}function Sm({a:e}){return(0,r.jsxs)("div",{className:"mc-page",style:{padding:5,gap:3},children:[(0,r.jsxs)("div",{style:{display:"flex",alignItems:"center",justifyContent:"space-between"},children:[(0,r.jsxs)("div",{children:[(0,r.jsx)("div",{style:x(6,800,"#0f172a"),children:"\u0422\u0430\u0440\u0438\u0444"}),(0,r.jsx)("div",{style:U(3.5),children:"\u0441\u043B\u0435\u0434. \u0441\u043F\u0438\u0441\u0430\u043D\u0438\u0435 \xB7 12 \u0438\u044E\u043D\u044F"})]}),(0,r.jsx)("div",{style:{...x(4.5,800,"white"),padding:"2.5px 7px",borderRadius:4,background:`linear-gradient(135deg, ${e}, #ff8a4d)`},children:"PRO"})]}),(0,r.jsxs)("div",{style:{background:`linear-gradient(135deg, ${e}20, ${e}08)`,borderRadius:5,padding:5,display:"flex",flexDirection:"column",gap:1.5,border:`1px solid ${e}40`,position:"relative",overflow:"hidden"},children:[(0,r.jsx)("div",{style:{position:"absolute",top:-10,right:-10,width:30,height:30,borderRadius:"50%",background:`${e}30`,filter:"blur(10px)"}}),(0,r.jsxs)("div",{style:{display:"flex",alignItems:"baseline",gap:2,position:"relative"},children:[(0,r.jsx)("span",{style:x(11.5,900,"#0f172a"),children:"2 490 \u20BD"}),(0,r.jsx)("span",{style:x(5,600,"#64748b"),children:"/ \u043C\u0435\u0441\u044F\u0446"})]}),(0,r.jsxs)("div",{style:{display:"flex",alignItems:"center",gap:2,position:"relative"},children:[(0,r.jsx)(V,{d:$.shield,size:4.5,color:e}),(0,r.jsx)("span",{style:x(4,500,"#475569"),children:"\u043E\u043F\u043B\u0430\u0447\u0435\u043D\u043E \u0434\u043E 12.06 \xB7 \u0430\u0432\u0442\u043E\u043F\u0440\u043E\u0434\u043B\u0435\u043D\u0438\u0435"})]})]}),(0,r.jsx)("div",{style:{display:"flex",flexDirection:"column",gap:2},children:[{n:"\u041F\u0440\u043E\u0435\u043A\u0442\u044B",v:"\u221E",on:!0},{n:"\u041F\u0440\u043E\u043C\u043F\u0442\u043E\u0432 \u0432 \u0434\u0435\u043D\u044C",v:"1 240 / 5k",on:!0},{n:"\u0427\u0430\u0442 \u0441 \u0438\u043D\u0436\u0435\u043D\u0435\u0440\u043E\u043C",v:"< 1 \u0447\u0430\u0441\u0430",on:!0},{n:"\u042D\u043A\u0441\u043F\u043E\u0440\u0442 \u043A\u043E\u0434\u0430",v:"Next.js",on:!0}].map((t,n)=>(0,r.jsxs)("div",{style:{display:"flex",alignItems:"center",gap:3},children:[(0,r.jsx)(V,{d:$.check,size:5,color:e,stroke:2.4}),(0,r.jsx)("span",{style:x(4,500,"#475569",{flex:1}),children:t.n}),(0,r.jsx)("span",{style:x(4,700,"#0f172a"),children:t.v})]},n))})]})}var Qe=[{t:"\u0421\u0434\u0435\u043B\u0430\u0439 \u043B\u0435\u043D\u0434\u0438\u043D\u0433 \u0434\u043B\u044F \u0431\u0430\u0440\u0431\u0435\u0440\u0448\u043E\u043F\u0430 \u0441 \u043E\u043D\u043B\u0430\u0439\u043D-\u0437\u0430\u043F\u0438\u0441\u044C\u044E",tag:"\u0432\u0438\u0437\u0438\u0442\u043A\u0430",icon:"\u2702",kind:"visitka",url:"razor-barber.omnia.app"},{t:"\u041C\u0430\u0433\u0430\u0437\u0438\u043D \u043E\u0434\u0435\u0436\u0434\u044B \u0441 \u0421\u0411\u041F-\u043E\u043F\u043B\u0430\u0442\u043E\u0439 \u0438 \u0421\u0414\u042D\u041A",tag:"\u043C\u0430\u0433\u0430\u0437\u0438\u043D",icon:"\u{1F6CD}",kind:"shop",url:"pole-store.omnia.app"},{t:"\u0427\u0430\u0442 \u0434\u043B\u044F \u043E\u043D\u043B\u0430\u0439\u043D-\u0448\u043A\u043E\u043B\u044B \u0441 \u043A\u043E\u043C\u043D\u0430\u0442\u0430\u043C\u0438 \u0438 \u0444\u0430\u0439\u043B\u0430\u043C\u0438",tag:"\u043C\u0435\u0441\u0441\u0435\u043D\u0434\u0436\u0435\u0440",icon:"\u{1F4AC}",kind:"chat",url:"lyceum7.omnia.app"},{t:"Telegram-\u0431\u043E\u0442 \u043F\u043E \u043C\u043E\u0435\u0439 \u0431\u0430\u0437\u0435 \u0437\u043D\u0430\u043D\u0438\u0439 \u0441 RAG-\u043F\u043E\u0438\u0441\u043A\u043E\u043C",tag:"\u0418\u0418-\u0431\u043E\u0442",icon:"\u{1F9E0}",kind:"bot",url:"knowledge-bot.omnia.app"},{t:"SaaS-\u043A\u0430\u0431\u0438\u043D\u0435\u0442 \u043A\u043B\u0438\u0435\u043D\u0442\u043E\u0432 \u0441 \u0440\u043E\u043B\u044F\u043C\u0438 \u0438 \u043F\u043E\u0434\u043F\u0438\u0441\u043A\u0430\u043C\u0438",tag:"SaaS",icon:"\u26A1",kind:"saas",url:"omnia-cabinet.app"}];function Cm(){let[e,t]=I(0),[n,a]=I(""),[i,o]=I(!0),s=wn();W(()=>{o(!0);let c=setTimeout(()=>o(!1),1300);return()=>clearTimeout(c)},[e]),W(()=>{let c,p=Qe[e].t,f=0;a("");let g=()=>{f++,a(p.slice(0,f)),f<p.length?c=setTimeout(g,26+Math.random()*22):c=setTimeout(()=>t(v=>(v+1)%Qe.length),2200)};return c=setTimeout(g,400),()=>clearTimeout(c)},[e]);let l=ot(null);return W(()=>{if(s)return;let c=l.current;if(!c)return;let p,f=g=>{let v=c.getBoundingClientRect(),y=((g.clientX-v.left)/v.width-.5)*2,w=((g.clientY-v.top)/v.height-.5)*2;cancelAnimationFrame(p),p=requestAnimationFrame(()=>{c.style.setProperty("--mx",y),c.style.setProperty("--my",w)})};return c.addEventListener("mousemove",f),()=>{c.removeEventListener("mousemove",f),cancelAnimationFrame(p)}},[s]),(0,r.jsxs)("section",{className:"hv6",ref:l,children:[(0,r.jsx)("style",{children:`
        .hv6 {
          position: relative; min-height: 100vh;
          padding: 110px 24px 60px;
          display: flex; flex-direction: column; justify-content: center;
          overflow: hidden;
          --mx: 0; --my: 0;
        }

        /* ===== animated mesh background ===== */
        .hv6-mesh {
          position: absolute; inset: -10%;
          pointer-events: none; z-index: 0;
          overflow: hidden;
        }
        .hv6-mesh .blob {
          position: absolute; border-radius: 50%;
          filter: blur(80px);
          mix-blend-mode: screen;
          will-change: transform;
        }
        .hv6-mesh .b1 {
          width: 60vw; height: 60vw; max-width: 900px; max-height: 900px;
          left: -8vw; top: -10vw;
          background: radial-gradient(circle, rgba(124,92,255,0.6), transparent 65%);
          animation: hv6-orb-1 24s ease-in-out infinite;
        }
        .hv6-mesh .b2 {
          width: 55vw; height: 55vw; max-width: 820px; max-height: 820px;
          right: -10vw; top: 5vw;
          background: radial-gradient(circle, rgba(236,76,184,0.42), transparent 65%);
          animation: hv6-orb-2 28s ease-in-out infinite;
        }
        .hv6-mesh .b3 {
          width: 70vw; height: 60vw; max-width: 1100px; max-height: 900px;
          left: 20%; bottom: -20vw;
          background: radial-gradient(circle, rgba(78,213,227,0.32), transparent 65%);
          animation: hv6-orb-3 32s ease-in-out infinite;
        }
        @keyframes hv6-orb-1 {
          0%,100% { transform: translate(0,0) scale(1); }
          50%     { transform: translate(120px, 80px) scale(1.15); }
        }
        @keyframes hv6-orb-2 {
          0%,100% { transform: translate(0,0) scale(1); }
          50%     { transform: translate(-100px, 60px) scale(0.9); }
        }
        @keyframes hv6-orb-3 {
          0%,100% { transform: translate(0,0) scale(1); }
          33%     { transform: translate(-80px, -60px) scale(1.08); }
          66%     { transform: translate(100px, 40px) scale(0.94); }
        }
        .hv6-mesh .grid {
          position: absolute; inset: 0;
          background-image:
            linear-gradient(rgba(124, 92, 255, 0.04) 1px, transparent 1px),
            linear-gradient(90deg, rgba(124, 92, 255, 0.04) 1px, transparent 1px);
          background-size: 80px 80px;
          mask-image: radial-gradient(ellipse 70% 60% at 50% 30%, black 30%, transparent 85%);
        }
        .hv6-mesh .grain {
          position: absolute; inset: 0; opacity: 0.03;
          background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='1.4' numOctaves='2'/%3E%3C/filter%3E%3Crect width='200' height='200' filter='url(%23n)'/%3E%3C/svg%3E");
          mix-blend-mode: overlay;
        }

        /* ===== content layer ===== */
        .hv6-inner {
          position: relative; z-index: 2;
          max-width: 1280px; margin: 0 auto; width: 100%;
          display: grid; grid-template-columns: 1.15fr 1fr; gap: 64px;
          align-items: center;
        }
        .hv6-text { min-width: 0; }

        .hv6-eyebrow {
          display: inline-flex; align-items: center; gap: 9px;
          padding: 7px 16px 7px 8px; border-radius: 999px;
          background: rgba(20, 20, 27, 0.55);
          backdrop-filter: blur(18px) saturate(160%);
          border: 1px solid rgba(255,255,255,0.10);
          font-size: 13px; color: rgba(255,255,255,0.85);
          margin-bottom: 26px;
          font-weight: 500;
        }
        .hv6-eyebrow .pill {
          background: linear-gradient(135deg, #7c5cff, #c66dff);
          color: white; padding: 3px 9px; border-radius: 999px;
          font-size: 10.5px; font-weight: 800; letter-spacing: 0.06em;
        }
        .hv6-eyebrow .pulse {
          width: 8px; height: 8px; border-radius: 50%;
          background: #4cd9a4;
          box-shadow: 0 0 0 4px rgba(76, 217, 164, 0.18), 0 0 12px rgba(76, 217, 164, 0.5);
          animation: pulse-dot 1.6s infinite;
        }

        .hv6-title {
          font-size: clamp(40px, 5.2vw, 80px);
          line-height: 0.98;
          letter-spacing: -0.045em;
          font-weight: 700;
          margin: 0 0 24px;
          color: var(--fg);
          max-width: 14ch;
          font-feature-settings: "ss01", "cv11";
        }
        .hv6-title .ln { display: block; white-space: nowrap; }
        .hv6-grad {
          background: linear-gradient(105deg, #7c5cff 0%, #c66dff 30%, #ffffff 50%, #5cb8ff 70%, #7c5cff 100%);
          background-size: 280% 100%;
          -webkit-background-clip: text; background-clip: text;
          color: transparent; -webkit-text-fill-color: transparent;
          animation: hv6-shiny 4s linear infinite;
          display: inline-block;
        }
        @keyframes hv6-shiny {
          0%   { background-position: 200% 0%; }
          100% { background-position: -200% 0%; }
        }

        .hv6-sub {
          font-size: clamp(16px, 1.3vw, 19px);
          line-height: 1.55;
          color: var(--fg-2);
          max-width: 50ch;
          margin: 0 0 32px;
        }

        .hv6-cta {
          display: flex; flex-wrap: wrap; gap: 12px; align-items: center;
          margin-bottom: 28px;
        }
        .hv6-btn-primary {
          display: inline-flex; align-items: center; gap: 9px;
          padding: 17px 28px; border-radius: 999px;
          background: linear-gradient(135deg, #7c5cff, #a48aff);
          color: white; font-size: 16px; font-weight: 600;
          border: 0; cursor: pointer;
          box-shadow: 0 20px 50px -16px rgba(124, 92, 255, 0.7),
                      0 0 0 1px rgba(255,255,255,0.08) inset;
          transition: transform .2s cubic-bezier(.2,.8,.2,1), box-shadow .25s;
          font-family: inherit;
        }
        .hv6-btn-primary:hover {
          transform: translateY(-2px);
          box-shadow: 0 28px 64px -16px rgba(124, 92, 255, 0.85),
                      0 0 0 1px rgba(255,255,255,0.18) inset;
        }
        .hv6-btn-ghost {
          display: inline-flex; align-items: center; gap: 7px;
          padding: 16px 22px; border-radius: 999px;
          background: rgba(255,255,255,0.04);
          backdrop-filter: blur(12px);
          color: var(--fg-2); font-size: 15.5px; font-weight: 500;
          border: 1px solid rgba(255,255,255,0.10);
          transition: all .2s;
        }
        .hv6-btn-ghost:hover { color: var(--fg); border-color: rgba(255,255,255,0.22); background: rgba(255,255,255,0.07); }

        /* live counter strip */
        .hv6-live {
          display: inline-flex; align-items: center; gap: 14px;
          padding: 10px 16px 10px 12px; border-radius: 999px;
          background: rgba(20, 20, 27, 0.55);
          backdrop-filter: blur(18px);
          border: 1px solid rgba(255,255,255,0.07);
          font-size: 13.5px; color: var(--fg-2);
          margin-bottom: 0;
        }
        .hv6-live .ava-stack { display: flex; }
        .hv6-live .ava-stack span {
          width: 22px; height: 22px; border-radius: 50%;
          margin-left: -8px;
          border: 2px solid var(--bg);
          background: linear-gradient(135deg, var(--c1), var(--c2));
          font-size: 0;
        }
        .hv6-live .ava-stack span:first-child { margin-left: 0; }
        .hv6-live b {
          color: var(--fg); font-weight: 700; font-variant-numeric: tabular-nums;
          background: linear-gradient(135deg, #7c5cff, #c66dff);
          -webkit-background-clip: text; background-clip: text;
          color: transparent; -webkit-text-fill-color: transparent;
        }

        /* ===== RIGHT: interactive prompt mockup ===== */
        .hv6-promo {
          position: relative;
          transform: perspective(1200px) rotateY(calc(var(--mx) * -3deg)) rotateX(calc(var(--my) * 3deg));
          transition: transform .2s cubic-bezier(.2,.8,.2,1);
          will-change: transform;
        }
        .hv6-promo-glow {
          position: absolute; inset: -30px;
          background: radial-gradient(circle, rgba(124,92,255,0.4), transparent 60%);
          filter: blur(40px);
          z-index: 0;
        }
        .hv6-promo-card {
          position: relative; z-index: 1;
          background: rgba(20, 20, 27, 0.7);
          backdrop-filter: blur(28px) saturate(180%);
          border: 1px solid rgba(255,255,255,0.10);
          border-radius: 22px;
          padding: 22px;
          box-shadow:
            0 60px 100px -40px rgba(0,0,0,0.6),
            0 0 0 1px rgba(255,255,255,0.03) inset;
        }
        .hv6-promo-head {
          display: flex; align-items: center; gap: 9px;
          padding-bottom: 14px;
          border-bottom: 1px solid rgba(255,255,255,0.07);
          margin-bottom: 14px;
        }
        .hv6-promo-head .lights { display: flex; gap: 5px; }
        .hv6-promo-head .lights span {
          width: 10px; height: 10px; border-radius: 50%;
          background: rgba(255,255,255,0.1);
        }
        .hv6-promo-head .title {
          font-family: var(--mono); font-size: 11.5px;
          color: rgba(255,255,255,0.55);
          margin-left: 6px;
        }
        .hv6-promo-head .tab {
          margin-left: auto;
          font-size: 10.5px; padding: 3px 9px; border-radius: 999px;
          background: rgba(124,92,255,0.15); color: #b8a5ff;
          border: 1px solid rgba(124,92,255,0.3);
          font-weight: 600; font-family: var(--mono); letter-spacing: 0.04em;
        }

        .hv6-promo-prompt-wrap {
          background: rgba(8, 8, 12, 0.6);
          border: 1px solid rgba(255,255,255,0.05);
          border-radius: 14px;
          padding: 14px 16px;
          margin-bottom: 12px;
          min-height: 100px;
        }
        .hv6-promo-prompt-meta {
          display: flex; align-items: center; gap: 8px;
          font-family: var(--mono); font-size: 10.5px; color: rgba(255,255,255,0.4);
          margin-bottom: 8px;
        }
        .hv6-promo-prompt-meta .dot {
          width: 6px; height: 6px; border-radius: 50%;
          background: #7c5cff;
          box-shadow: 0 0 8px #7c5cff;
          animation: pulse-dot 1.4s infinite;
        }
        .hv6-promo-prompt-text {
          font-size: 15px; line-height: 1.45;
          color: var(--fg);
          font-weight: 500;
        }
        .hv6-cursor {
          display: inline-block; width: 2px; height: 16px;
          background: #7c5cff; vertical-align: -2px;
          margin-left: 2px;
          animation: typing-cursor 0.7s steps(1) infinite;
        }

        .hv6-promo-tags {
          display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 14px;
        }
        .hv6-promo-tag {
          font-size: 11.5px; padding: 5px 11px; border-radius: 999px;
          background: rgba(255,255,255,0.04); color: rgba(255,255,255,0.65);
          border: 1px solid rgba(255,255,255,0.06);
          font-family: var(--mono); letter-spacing: 0.02em;
          transition: all .2s;
        }
        .hv6-promo-tag.active {
          background: linear-gradient(135deg, rgba(124,92,255,0.25), rgba(236,76,184,0.18));
          border-color: rgba(124,92,255,0.55);
          color: #d8c8ff;
          box-shadow: 0 4px 12px -3px rgba(124,92,255,0.45);
        }
        .hv6-promo-tag:not(.active):hover {
          background: rgba(255,255,255,0.07);
          color: rgba(255,255,255,0.85);
          border-color: rgba(255,255,255,0.12);
        }
        /* output label above 3 cards */
        .hv6-promo-out-label {
          display: flex; align-items: center; justify-content: space-between;
          font-family: var(--mono); font-size: 10.5px;
          color: rgba(255,255,255,0.45);
          margin: 4px 2px 6px;
          letter-spacing: 0.04em; text-transform: uppercase;
        }
        .hv6-promo-out-label .left { display: inline-flex; align-items: center; gap: 6px; }
        .hv6-promo-out-label .dot {
          width: 5px; height: 5px; border-radius: 50%;
          background: #4cd9a4;
          box-shadow: 0 0 6px #4cd9a4;
          animation: pulse-dot 1.6s infinite;
        }
        .hv6-promo-out-label .tokens {
          color: #b8a5ff;
          font-weight: 700;
        }

        .hv6-promo-output {
          display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px;
          margin-bottom: 14px;
        }
        .hv6-promo-out {
          aspect-ratio: 4/4.4; border-radius: 12px;
          background: var(--card-grad, linear-gradient(135deg, #7c5cff, #c66dff));
          position: relative; overflow: hidden;
          padding: 7px;
          cursor: pointer;
          transition: transform .3s cubic-bezier(.2,.8,.2,1), box-shadow .3s;
          box-shadow: 0 4px 12px -4px rgba(0,0,0,0.4);
          animation: card-pop-in 0.55s cubic-bezier(.2,1,.3,1) both;
        }
        /* shimmer sweep across card during generation */
        .hv6-promo-out::after {
          content: ''; position: absolute; inset: 0;
          background: linear-gradient(105deg, transparent 30%, rgba(255,255,255,0.55) 50%, transparent 70%);
          opacity: 0; pointer-events: none;
          z-index: 5;
        }
        .hv6-promo-out.generating::after {
          animation: shimmer-sweep 1.2s cubic-bezier(.2,.8,.2,1) forwards;
        }
        @keyframes shimmer-sweep {
          from { transform: translateX(-130%); opacity: 0.9; }
          to   { transform: translateX(130%);  opacity: 0;   }
        }
        .hv6-promo-out.generating .mc-page { filter: blur(0.4px); }
        /* live URL pill below cards */
        .hv6-promo-url {
          display: flex; align-items: center; gap: 7px;
          margin-top: 4px;
          padding: 7px 12px 7px 9px;
          background: rgba(8, 8, 12, 0.7);
          backdrop-filter: blur(14px);
          border: 1px solid rgba(76, 217, 164, 0.25);
          border-radius: 999px;
          opacity: 0; transform: translateY(6px);
          animation: url-in 0.4s cubic-bezier(.2,.8,.2,1) 1.2s forwards;
        }
        .hv6-promo-out.generating ~ .hv6-promo-url { animation: none; opacity: 0; }
        @keyframes url-in {
          to { opacity: 1; transform: translateY(0); }
        }
        .hv6-promo-url .lock {
          width: 12px; height: 12px; border-radius: 50%;
          background: #4cd9a4;
          box-shadow: 0 0 8px #4cd9a4;
          flex-shrink: 0;
          position: relative;
        }
        .hv6-promo-url .lock::after {
          content: ''; position: absolute; left: 50%; top: 50%;
          transform: translate(-50%, -50%); width: 5px; height: 5px;
          border-radius: 50%; background: #0d0d12;
        }
        .hv6-promo-url .scheme {
          font-family: 'JetBrains Mono', monospace;
          font-size: 10.5px; color: #4cd9a4; font-weight: 700;
        }
        .hv6-promo-url .host {
          font-family: 'JetBrains Mono', monospace;
          font-size: 11px; color: white; font-weight: 600;
          letter-spacing: 0.01em;
          flex: 1; min-width: 0;
          overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        }
        .hv6-promo-url .open {
          font-size: 10.5px; color: rgba(255,255,255,0.6); font-weight: 600;
          display: inline-flex; align-items: center; gap: 3px;
          padding: 2px 8px; border-radius: 999px;
          background: rgba(255,255,255,0.08);
          border: 1px solid rgba(255,255,255,0.1);
          flex-shrink: 0;
        }
        @keyframes card-pop-in {
          from { opacity: 0; transform: translateY(10px) scale(0.94); }
          to   { opacity: 1; transform: translateY(0) scale(1); }
        }
        /* shared base for all mini-mockups */
        .mc-page {
          position: relative; z-index: 2;
          width: 100%; height: 100%;
          background: rgba(255,255,255,0.97);
          border-radius: 6px;
          padding: 6px 7px;
          display: flex; flex-direction: column; gap: 3px;
          box-shadow: 0 1px 0 rgba(0,0,0,0.08);
          overflow: hidden;
        }
        .hv6-promo-out:hover {
          transform: translateY(-3px) scale(1.02);
          box-shadow: 0 14px 28px -8px rgba(0,0,0,0.55);
        }
        .hv6-promo-out.active {
          outline: 2px solid white;
          outline-offset: 2px;
        }
        .hv6-promo-out::before {
          content: ''; position: absolute; inset: 0;
          background:
            radial-gradient(circle at 25% 20%, rgba(255,255,255,0.4), transparent 55%),
            radial-gradient(circle at 80% 90%, rgba(0,0,0,0.3), transparent 60%);
          mix-blend-mode: overlay;
          pointer-events: none;
        }
        .hv6-promo-out .v-tag {
          position: absolute; top: 6px; right: 6px; z-index: 3;
          background: rgba(255,255,255,0.95); color: #1a1a26;
          font-family: var(--mono); font-size: 8.5px; font-weight: 700;
          padding: 2px 5px; border-radius: 4px;
          letter-spacing: 0.04em;
        }
        .hv6-promo-out .mock {
          position: relative; z-index: 2;
          width: 100%; height: 100%;
          background: rgba(255,255,255,0.94);
          border-radius: 5px;
          padding: 6px 7px;
          display: flex; flex-direction: column; gap: 4px;
          box-shadow: 0 1px 0 rgba(0,0,0,0.08);
        }
        /* MOCK 1: landing \u2014 centered hero with cta */
        .hv6-promo-out .mock.landing {
          align-items: center; justify-content: center; gap: 5px;
        }
        .hv6-promo-out .mock.landing .nav {
          position: absolute; top: 5px; left: 6px; right: 6px;
          display: flex; align-items: center; justify-content: space-between;
        }
        .hv6-promo-out .mock.landing .nav .logo {
          width: 4px; height: 4px; border-radius: 50%; background: #7c5cff;
        }
        .hv6-promo-out .mock.landing .nav .dots { display: flex; gap: 2px; }
        .hv6-promo-out .mock.landing .nav .dots span {
          width: 8px; height: 2px; border-radius: 1px; background: rgba(0,0,0,0.18);
        }
        .hv6-promo-out .mock.landing .h1 {
          width: 70%; height: 6px; border-radius: 2px;
          background: linear-gradient(90deg, #1a1a26, #7c5cff);
        }
        .hv6-promo-out .mock.landing .h2 {
          width: 50%; height: 4px; border-radius: 2px; background: rgba(0,0,0,0.4);
        }
        .hv6-promo-out .mock.landing .btn {
          margin-top: 3px;
          padding: 3px 8px;
          border-radius: 999px;
          background: #7c5cff;
          color: white; font-size: 6px; font-weight: 700;
        }

        /* MOCK 2: shop \u2014 product grid */
        .hv6-promo-out .mock.shop {
          padding: 5px;
        }
        .hv6-promo-out .mock.shop .top {
          display: flex; gap: 3px; margin-bottom: 4px;
        }
        .hv6-promo-out .mock.shop .pill {
          padding: 1.5px 4px; border-radius: 6px;
          background: rgba(0,0,0,0.08);
          font-size: 5px; font-weight: 700; color: #555;
        }
        .hv6-promo-out .mock.shop .pill.on {
          background: #5cb8ff; color: white;
        }
        .hv6-promo-out .mock.shop .grid {
          display: grid; grid-template-columns: 1fr 1fr; gap: 3px;
          flex: 1;
        }
        .hv6-promo-out .mock.shop .cell {
          background: linear-gradient(135deg, #5cb8ff20, #7c5cff20);
          border-radius: 3px;
          display: flex; flex-direction: column; justify-content: flex-end;
          padding: 2px 3px;
        }
        .hv6-promo-out .mock.shop .cell .pr {
          width: 60%; height: 2.5px; border-radius: 1px;
          background: #1a1a26;
        }

        /* MOCK 3: app \u2014 mobile list */
        .hv6-promo-out .mock.app {
          padding: 4px;
        }
        .hv6-promo-out .mock.app .phone {
          width: 100%; height: 100%;
          background: linear-gradient(180deg, #0d0d12 0%, #1c1c25 100%);
          border-radius: 6px;
          padding: 4px 5px;
          display: flex; flex-direction: column; gap: 3px;
        }
        .hv6-promo-out .mock.app .status {
          display: flex; align-items: center; justify-content: space-between;
        }
        .hv6-promo-out .mock.app .status .time {
          width: 8px; height: 2.5px; border-radius: 1px; background: white;
        }
        .hv6-promo-out .mock.app .status .bat {
          width: 5px; height: 2.5px; border-radius: 1px; background: #4cd9a4;
        }
        .hv6-promo-out .mock.app .title {
          width: 50%; height: 3px; border-radius: 1.5px; background: white;
          margin: 2px 0;
        }
        .hv6-promo-out .mock.app .row {
          display: flex; align-items: center; gap: 3px;
          padding: 2px 0;
          border-bottom: 0.5px solid rgba(255,255,255,0.06);
        }
        .hv6-promo-out .mock.app .row .ava {
          width: 5px; height: 5px; border-radius: 50%;
          background: linear-gradient(135deg, #4cd9a4, #5cb8ff);
        }
        .hv6-promo-out .mock.app .row .lines {
          display: flex; flex-direction: column; gap: 1px; flex: 1;
        }
        .hv6-promo-out .mock.app .row .lines .l1 {
          width: 70%; height: 2px; background: rgba(255,255,255,0.9); border-radius: 1px;
        }
        .hv6-promo-out .mock.app .row .lines .l2 {
          width: 45%; height: 1.5px; background: rgba(255,255,255,0.4); border-radius: 1px;
        }
        .hv6-promo-out .mock.app .row .badge {
          width: 5px; height: 3px; border-radius: 1.5px; background: #4cd9a4;
        }

        .hv6-promo-out:nth-child(2) { --c1: #5cb8ff; --c2: #7c5cff; }
        .hv6-promo-out:nth-child(3) { --c1: #4cd9a4; --c2: #5cb8ff; }

        .hv6-promo-go {
          width: 100%; padding: 12px;
          background: linear-gradient(135deg, #7c5cff, #a48aff);
          color: white; border: 0; border-radius: 11px;
          font-size: 14px; font-weight: 600;
          display: inline-flex; align-items: center; justify-content: center; gap: 7px;
          cursor: pointer;
          box-shadow: 0 12px 28px -8px rgba(124, 92, 255, 0.55);
          font-family: inherit;
        }

        /* floating side chips (3D) */
        .hv6-chip-float {
          position: absolute;
          padding: 9px 16px;
          backdrop-filter: blur(14px) saturate(160%);
          background: rgba(20, 20, 27, 0.85);
          border: 1px solid rgba(255,255,255,0.08);
          border-radius: 999px;
          font-size: 12.5px; font-weight: 500;
          color: var(--fg-2);
          display: inline-flex; align-items: center; gap: 9px;
          box-shadow: 0 16px 36px -10px rgba(0,0,0,0.5);
          z-index: 3;
          white-space: nowrap;
        }
        .hv6-chip-float .ic { font-size: 14px; }
        .hv6-chip-float.f1 {
          left: -20%; top: 4%;
          transform: translate(calc(var(--mx) * 12px), calc(var(--my) * 12px));
          transition: transform .25s cubic-bezier(.2,.8,.2,1);
        }
        .hv6-chip-float.f2 {
          right: -16%; top: 14%;
          transform: translate(calc(var(--mx) * -16px), calc(var(--my) * -8px));
          transition: transform .25s cubic-bezier(.2,.8,.2,1);
        }
        .hv6-chip-float.f3 {
          left: -14%; bottom: 8%;
          transform: translate(calc(var(--mx) * -10px), calc(var(--my) * 14px));
          transition: transform .25s cubic-bezier(.2,.8,.2,1);
        }
        .hv6-chip-float .dot {
          width: 7px; height: 7px; border-radius: 50%;
          background: var(--dot-c, #4cd9a4);
          box-shadow: 0 0 8px var(--dot-c, #4cd9a4);
          animation: pulse-dot 1.6s infinite;
        }

        @media (max-width: 1100px) {
          .hv6-inner { grid-template-columns: 1fr; gap: 48px; }
          .hv6-chip-float { display: none; }
          .hv6-title { font-size: clamp(40px, 9vw, 76px); }
        }
        @media (max-width: 640px) {
          .hv6 { padding: 100px 20px 60px; }
        }
      `}),(0,r.jsxs)("div",{className:"hv6-mesh","aria-hidden":!0,children:[(0,r.jsx)("div",{className:"blob b1"}),(0,r.jsx)("div",{className:"blob b2"}),(0,r.jsx)("div",{className:"blob b3"}),(0,r.jsx)("div",{className:"grid"}),(0,r.jsx)("div",{className:"grain"})]}),(0,r.jsxs)("div",{className:"hv6-inner",children:[(0,r.jsxs)("div",{className:"hv6-text",children:[(0,r.jsxs)("div",{className:"hv6-eyebrow",children:[(0,r.jsx)("span",{className:"pulse"}),(0,r.jsx)("span",{className:"pill",children:"v2.4"}),"\u0421\u0435\u0440\u0432\u0435\u0440\u044B \u0432 \u0420\u0424. \u0411\u0435\u0437 VPN. \u0412 \u0440\u0443\u0431\u043B\u044F\u0445."]}),(0,r.jsxs)("h1",{className:"hv6-title",children:[(0,r.jsx)("span",{className:"ln",children:"\u0412\u0435\u0431-\u043F\u0440\u0438\u043B\u043E\u0436\u0435\u043D\u0438\u0435"}),(0,r.jsx)("span",{className:"ln",children:(0,r.jsx)("span",{className:"hv6-grad",children:"\u0437\u0430 \u043E\u0434\u0438\u043D \u0432\u0435\u0447\u0435\u0440."})})]}),(0,r.jsx)("p",{className:"hv6-sub",children:"\u0418\u0418-\u043A\u043E\u043D\u0441\u0442\u0440\u0443\u043A\u0442\u043E\u0440 \u0434\u043B\u044F \u0440\u043E\u0441\u0441\u0438\u0439\u0441\u043A\u043E\u0433\u043E \u0440\u044B\u043D\u043A\u0430. \u041E\u043F\u0438\u0448\u0438\u0442\u0435 \u0438\u0434\u0435\u044E \u2014 Omnia \u0441\u043E\u0431\u0435\u0440\u0451\u0442 \u0441\u0430\u0439\u0442, \u043C\u0435\u0441\u0441\u0435\u043D\u0434\u0436\u0435\u0440, \u0431\u043E\u0442\u0430 \u0438\u043B\u0438 SaaS-\u043A\u0430\u0431\u0438\u043D\u0435\u0442, \u0437\u0430\u0434\u0435\u043F\u043B\u043E\u0438\u0442 \u043D\u0430 \u0432\u0430\u0448 \u0434\u043E\u043C\u0435\u043D \u0438 \u043E\u0441\u0442\u0430\u0432\u0438\u0442 \u043A\u043E\u0434."}),(0,r.jsxs)("div",{className:"hv6-cta",children:[(0,r.jsxs)("button",{className:"hv6-btn-primary",children:["\u041D\u0430\u0447\u0430\u0442\u044C \u0431\u0435\u0441\u043F\u043B\u0430\u0442\u043D\u043E",(0,r.jsx)("svg",{width:"16",height:"16",viewBox:"0 0 24 24",fill:"none",stroke:"currentColor",strokeWidth:"2.2",strokeLinecap:"round",strokeLinejoin:"round",children:(0,r.jsx)("path",{d:"M5 12h14M13 6l6 6-6 6"})})]}),(0,r.jsxs)("a",{href:"#demo",className:"hv6-btn-ghost",children:[(0,r.jsx)("svg",{width:"14",height:"14",viewBox:"0 0 24 24",fill:"currentColor",children:(0,r.jsx)("path",{d:"M6 4l14 8-14 8V4z"})}),"\u0414\u0435\u043C\u043E \u2014 26 \u0441\u0435\u043A"]})]}),(0,r.jsxs)("div",{className:"hv6-live",children:[(0,r.jsxs)("div",{className:"ava-stack",children:[(0,r.jsx)("span",{style:{"--c1":"#7c5cff","--c2":"#c66dff"}}),(0,r.jsx)("span",{style:{"--c1":"#5cb8ff","--c2":"#7c5cff"}}),(0,r.jsx)("span",{style:{"--c1":"#4cd9a4","--c2":"#5cb8ff"}}),(0,r.jsx)("span",{style:{"--c1":"#ec4cb8","--c2":"#7c5cff"}})]}),(0,r.jsxs)("span",{children:[(0,r.jsx)(Mm,{target:127})," \u0447\u0435\u043B\u043E\u0432\u0435\u043A \u0441\u0442\u0440\u043E\u044F\u0442 \u043F\u0440\u044F\u043C\u043E \u0441\u0435\u0439\u0447\u0430\u0441"]})]})]}),(0,r.jsxs)("div",{className:"hv6-promo",children:[(0,r.jsx)("div",{className:"hv6-promo-glow"}),(0,r.jsxs)("div",{className:"hv6-promo-card",children:[(0,r.jsxs)("div",{className:"hv6-promo-head",children:[(0,r.jsxs)("div",{className:"lights",children:[(0,r.jsx)("span",{}),(0,r.jsx)("span",{}),(0,r.jsx)("span",{})]}),(0,r.jsx)("span",{className:"title",children:"omnia \xB7 prompt"}),(0,r.jsx)("span",{className:"tab",children:"claude 4.5"})]}),(0,r.jsxs)("div",{className:"hv6-promo-prompt-wrap",children:[(0,r.jsxs)("div",{className:"hv6-promo-prompt-meta",children:[(0,r.jsx)("span",{className:"dot"}),Qe[e].icon," ",Qe[e].tag]}),(0,r.jsxs)("div",{className:"hv6-promo-prompt-text",children:[n,(0,r.jsx)("span",{className:"hv6-cursor"})]})]}),(0,r.jsx)("div",{className:"hv6-promo-tags",children:Qe.map((c,p)=>(0,r.jsx)("span",{className:`hv6-promo-tag ${p===e?"active":""}`,children:c.tag},p))}),(0,r.jsxs)("div",{className:"hv6-promo-out-label",children:[(0,r.jsxs)("span",{className:"left",children:[(0,r.jsx)("span",{className:"dot"}),"3 \u0432\u0430\u0440\u0438\u0430\u043D\u0442\u0430 \xB7 \u0433\u043E\u0442\u043E\u0432\u044B"]}),(0,r.jsxs)("span",{children:[(0,r.jsx)("span",{className:"tokens",children:"2 480"})," \u0442\u043E\u043A\u0435\u043D\u043E\u0432"]})]}),(0,r.jsx)("div",{className:"hv6-promo-output",children:[1,2,3].map(c=>(0,r.jsxs)("div",{className:`hv6-promo-out ${c===1?"active":""} ${i?"generating":""}`,style:{"--card-grad":cd[Qe[e].kind],animationDelay:`${(c-1)*70}ms`},children:[(0,r.jsxs)("span",{className:"v-tag",children:["v",c]}),(0,r.jsx)(pd,{kind:Qe[e].kind,variant:c})]},`${Qe[e].kind}-${c}`))}),(0,r.jsxs)("div",{className:"hv6-promo-url",children:[(0,r.jsx)("span",{className:"lock"}),(0,r.jsx)("span",{className:"scheme",children:"https://"}),(0,r.jsx)("span",{className:"host",children:Qe[e].url}),(0,r.jsx)("span",{className:"open",children:"\u043E\u0442\u043A\u0440\u044B\u0442\u044C \u2192"})]},`url-${Qe[e].kind}`),(0,r.jsxs)("button",{className:"hv6-promo-go",children:["\u0421\u043E\u0431\u0440\u0430\u0442\u044C \u0441\u0430\u0439\u0442",(0,r.jsx)("svg",{width:"14",height:"14",viewBox:"0 0 24 24",fill:"none",stroke:"currentColor",strokeWidth:"2.2",strokeLinecap:"round",strokeLinejoin:"round",children:(0,r.jsx)("path",{d:"M5 12h14M13 6l6 6-6 6"})})]})]}),(0,r.jsxs)("div",{className:"hv6-chip-float f1",style:{"--dot-c":"#4cd9a4"},children:[(0,r.jsx)("span",{className:"dot"}),"\u0441\u0435\u0440\u0432\u0435\u0440 RU-1 \u0433\u043E\u0442\u043E\u0432"]}),(0,r.jsxs)("div",{className:"hv6-chip-float f2",style:{"--dot-c":"#7c5cff"},children:[(0,r.jsx)("span",{className:"dot"}),"\u0432\u0435\u0440\u0441\u0438\u044F v1.3 \u2014 \u0441\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u0430"]}),(0,r.jsxs)("div",{className:"hv6-chip-float f3",style:{"--dot-c":"#ffd166"},children:[(0,r.jsx)("span",{className:"dot"}),"\u0434\u0435\u043F\u043B\u043E\u0439 \u0447\u0435\u0440\u0435\u0437 8 \u0441\u0435\u043A\u0443\u043D\u0434"]})]})]})]})}function Mm({target:e=127}){let[t,n]=ld(e,1800,0);return(0,r.jsx)("b",{ref:t,children:n})}function Em(){return(0,r.jsxs)("nav",{className:"om-nav",children:[(0,r.jsx)("style",{children:`
        .om-nav {
          position: sticky; top: 0; z-index: 50;
          backdrop-filter: blur(14px) saturate(140%);
          background: var(--nav-bg);
          border-bottom: 1px solid var(--line);
        }
        .om-nav-inner {
          max-width: 1240px; margin: 0 auto; padding: 0 32px;
          height: 64px; display: flex; align-items: center; justify-content: space-between;
        }
        .om-logo { display:flex; align-items:center; gap:9px; font-weight: 700; font-size: 17px; letter-spacing:-0.02em; }
        .om-logo-mark {
          width: 26px; height: 26px; border-radius: 8px;
          background: linear-gradient(135deg, var(--accent) 0%, #a48aff 100%);
          position: relative;
        }
        .om-logo-mark::after {
          content:''; position:absolute; inset: 6px; border-radius: 3px;
          background: white; opacity: 0.92;
          clip-path: polygon(0 60%, 30% 60%, 30% 0, 70% 0, 70% 100%, 100% 100%, 100% 40%, 0 40%);
        }
        .om-nav-links { display:flex; gap: 28px; font-size: 14px; color: var(--fg-2); }
        .om-nav-links a:hover { color: var(--accent); }
        .om-nav-cta { display: flex; gap: 8px; align-items: center; }
        .om-btn {
          display:inline-flex; align-items:center; gap: 7px;
          padding: 9px 16px; border-radius: 10px;
          font-size: 14px; font-weight: 500;
          border: 1px solid transparent; transition: all .15s;
        }
        .om-btn.ghost { color: var(--fg-2); }
        .om-btn.ghost:hover { background: var(--bg-2); color: var(--fg); }
        .om-btn.primary { background: var(--accent); color: white; }
        .om-btn.primary:hover { background: var(--accent-2); transform: translateY(-1px); box-shadow: 0 10px 24px -8px rgba(124, 92, 255, 0.5); }
        .om-btn.outline { border-color: var(--line); color: var(--fg); background: var(--bg); }
        .om-btn.outline:hover { border-color: var(--fg); }
        .om-btn.lg { padding: 14px 22px; font-size: 15px; border-radius: 12px; }
        @media (max-width: 760px) {
          .om-nav-links { display: none; }
          .om-nav-inner { padding: 0 20px; }
        }
      `}),(0,r.jsxs)("div",{className:"om-nav-inner",children:[(0,r.jsxs)("a",{href:"#",className:"om-logo",children:[(0,r.jsx)("div",{className:"om-logo-mark"}),"omnia"]}),(0,r.jsxs)("div",{className:"om-nav-links",children:[(0,r.jsx)("a",{href:"#how",children:"\u041A\u0430\u043A \u0440\u0430\u0431\u043E\u0442\u0430\u0435\u0442"}),(0,r.jsx)("a",{href:"#features",children:"\u0412\u043E\u0437\u043C\u043E\u0436\u043D\u043E\u0441\u0442\u0438"}),(0,r.jsx)("a",{href:"#compare",children:"\u0421\u0440\u0430\u0432\u043D\u0435\u043D\u0438\u0435"}),(0,r.jsx)("a",{href:"#pricing",children:"\u0422\u0430\u0440\u0438\u0444\u044B"}),(0,r.jsx)("a",{href:"#faq",children:"FAQ"})]}),(0,r.jsxs)("div",{className:"om-nav-cta",children:[(0,r.jsx)("a",{href:"#",className:"om-btn ghost",children:"\u0412\u043E\u0439\u0442\u0438"}),(0,r.jsx)("a",{href:"#",className:"om-btn primary",children:"\u041D\u0430\u0447\u0430\u0442\u044C \u0431\u0435\u0441\u043F\u043B\u0430\u0442\u043D\u043E"})]})]})]})}function Pm(){return(0,r.jsxs)("footer",{className:"om-footer",children:[(0,r.jsx)("style",{children:`
        .om-footer { background: var(--bg-2); border-top: 1px solid var(--line); padding: 64px 0 32px; }
        .om-footer-grid { display: grid; grid-template-columns: 1.6fr repeat(4, 1fr); gap: 48px; }
        .om-footer-brand .om-logo { margin-bottom: 14px; }
        .om-footer-tag { font-size: 13.5px; color: var(--muted); line-height: 1.55; max-width: 28ch; }
        .om-footer-col-title { font-size: 12px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: var(--fg-2); margin-bottom: 14px; }
        .om-footer-col a { display: block; font-size: 13.5px; color: var(--muted); padding: 5px 0; }
        .om-footer-col a:hover { color: var(--accent); }
        .om-footer-bottom {
          margin-top: 56px; padding-top: 24px; border-top: 1px solid var(--line);
          display: flex; align-items: center; justify-content: space-between;
          font-size: 12.5px; color: var(--muted); flex-wrap: wrap; gap: 14px;
        }
        @media (max-width: 880px) {
          .om-footer-grid { grid-template-columns: 1fr 1fr; }
        }
        @media (max-width: 560px) {
          .om-footer-grid { grid-template-columns: 1fr; }
        }
      `}),(0,r.jsxs)("div",{className:"container",children:[(0,r.jsxs)("div",{className:"om-footer-grid",children:[(0,r.jsxs)("div",{className:"om-footer-brand",children:[(0,r.jsxs)("a",{href:"#",className:"om-logo",children:[(0,r.jsx)("div",{className:"om-logo-mark"}),"omnia"]}),(0,r.jsx)("div",{className:"om-footer-tag",children:"\u0418\u0418-\u043F\u043B\u0430\u0442\u0444\u043E\u0440\u043C\u0430 \u0434\u043B\u044F \u0437\u0430\u043F\u0443\u0441\u043A\u0430 \u0432\u0435\u0431-\u043F\u0440\u0438\u043B\u043E\u0436\u0435\u043D\u0438\u0439. \u041E\u043F\u0438\u0448\u0438\u0442\u0435 \u0438\u0434\u0435\u044E \u2014 Omnia \u0441\u043E\u0431\u0435\u0440\u0451\u0442, \u0437\u0430\u0434\u0435\u043F\u043B\u043E\u0438\u0442 \u0438 \u043F\u043E\u0434\u0434\u0435\u0440\u0436\u0438\u0442."})]}),[{t:"\u041F\u0440\u043E\u0434\u0443\u043A\u0442",l:["\u0412\u043E\u0437\u043C\u043E\u0436\u043D\u043E\u0441\u0442\u0438","\u0422\u0430\u0440\u0438\u0444\u044B","\u0421\u0440\u0430\u0432\u043D\u0435\u043D\u0438\u0435","\u0418\u0437\u043C\u0435\u043D\u0435\u043D\u0438\u044F","\u0414\u043E\u0440\u043E\u0436\u043D\u0430\u044F \u043A\u0430\u0440\u0442\u0430"]},{t:"\u041F\u043B\u0430\u0442\u0444\u043E\u0440\u043C\u0430",l:["\u0421\u0435\u0440\u0432\u0435\u0440\u044B","\u0411\u0435\u0437\u043E\u043F\u0430\u0441\u043D\u043E\u0441\u0442\u044C","\u0421\u0442\u0430\u0442\u0443\u0441","API","\u0414\u043E\u043A\u0443\u043C\u0435\u043D\u0442\u0430\u0446\u0438\u044F"]},{t:"\u0420\u0435\u0441\u0443\u0440\u0441\u044B",l:["\u0411\u043B\u043E\u0433","\u0413\u0430\u0439\u0434\u044B","\u0428\u0430\u0431\u043B\u043E\u043D\u044B","\u0421\u043E\u043E\u0431\u0449\u0435\u0441\u0442\u0432\u043E","\u041F\u043E\u0434\u0434\u0435\u0440\u0436\u043A\u0430"]},{t:"\u041A\u043E\u043C\u043F\u0430\u043D\u0438\u044F",l:["\u041E \u043D\u0430\u0441","\u0412\u0430\u043A\u0430\u043D\u0441\u0438\u0438","\u041F\u0430\u0440\u0442\u043D\u0451\u0440\u044B","\u041A\u043E\u043D\u0442\u0430\u043A\u0442\u044B","\u041F\u0440\u0435\u0441\u0441-\u043A\u0438\u0442"]}].map(t=>(0,r.jsxs)("div",{className:"om-footer-col",children:[(0,r.jsx)("div",{className:"om-footer-col-title",children:t.t}),t.l.map(n=>(0,r.jsx)("a",{href:"#",children:n},n))]},t.t))]}),(0,r.jsxs)("div",{className:"om-footer-bottom",children:[(0,r.jsx)("div",{children:"\xA9 2026 Omnia Labs. \u0412\u0441\u0435 \u043F\u0440\u0430\u0432\u0430 \u0437\u0430\u0449\u0438\u0449\u0435\u043D\u044B."}),(0,r.jsxs)("div",{style:{display:"flex",gap:18},children:[(0,r.jsx)("a",{href:"#",style:{color:"inherit"},children:"\u041F\u043E\u043B\u0438\u0442\u0438\u043A\u0430 \u043A\u043E\u043D\u0444\u0438\u0434\u0435\u043D\u0446\u0438\u0430\u043B\u044C\u043D\u043E\u0441\u0442\u0438"}),(0,r.jsx)("a",{href:"#",style:{color:"inherit"},children:"\u0423\u0441\u043B\u043E\u0432\u0438\u044F \u0438\u0441\u043F\u043E\u043B\u044C\u0437\u043E\u0432\u0430\u043D\u0438\u044F"}),(0,r.jsx)("a",{href:"#",style:{color:"inherit"},children:"Cookies"})]})]})]})]})}var nd=[{n:"01",t:"\u041E\u043F\u0438\u0448\u0438\u0442\u0435 \u0441\u043B\u043E\u0432\u0430\u043C\u0438",tag:"\u043F\u0440\u043E\u043C\u043F\u0442",verb:"\u041F\u0438\u0448\u0443 \u043F\u0440\u043E\u043C\u043F\u0442\u2026",fact:"\u0431\u0435\u0437 \u0442\u0435\u0440\u043C\u0438\u043D\u0430\u043B\u0430, \u0431\u0435\u0437 YAML"},{n:"02",t:"\u0410\u0433\u0435\u043D\u0442 \u0441\u043E\u0431\u0438\u0440\u0430\u0435\u0442",tag:"\u043A\u043E\u0434 \xB7 \u0434\u0438\u0437\u0430\u0439\u043D",verb:"\u0413\u0435\u043D\u0435\u0440\u0438\u0440\u0443\u044E\u2026",fact:"\u0432\u0438\u0434\u043D\u043E \u043A\u0430\u0436\u0434\u044B\u0439 \u0444\u0430\u0439\u043B"},{n:"03",t:"\u041F\u0440\u0430\u0432\u044C\u0442\u0435 \u043D\u0430 \u043B\u0435\u0442\u0443",tag:"\u0432\u0435\u0440\u0441\u0438\u0438",verb:"\u0421\u043E\u0445\u0440\u0430\u043D\u044F\u044E v1.2",fact:"\u043E\u0442\u043A\u0430\u0442 \u0437\u0430 1 \u0441\u0435\u043A\u0443\u043D\u0434\u0443"},{n:"04",t:"\u0414\u0435\u043F\u043B\u043E\u0439 \u0432 \u043E\u0431\u043B\u0430\u043A\u043E",tag:"live",verb:"\u041F\u043E\u0434\u043D\u0438\u043C\u0430\u044E \u0441\u0435\u0440\u0432\u0435\u0440\u2026",fact:"SSL \u0438 \u0434\u043E\u043C\u0435\u043D \u0441\u0440\u0430\u0437\u0443"}];function Tm(){let[e,t]=I(0),[n,a]=I(!0);return W(()=>{if(!n)return;let i=setTimeout(()=>t(o=>(o+1)%nd.length),4500);return()=>clearTimeout(i)},[e,n]),(0,r.jsxs)("section",{id:"how",className:"hw13",children:[(0,r.jsx)("style",{children:`
        .hw13 {
          padding: 100px 24px;
          position: relative;
        }
        .hw13-inner {
          max-width: 1240px; margin: 0 auto;
        }
        .hw13-head {
          text-align: center; max-width: 720px; margin: 0 auto 48px;
        }
        .hw13-eyebrow {
          font-size: 12px; letter-spacing: 0.14em; text-transform: uppercase;
          color: var(--accent); font-weight: 700;
          display: inline-flex; align-items: center; gap: 10px; margin-bottom: 14px;
        }
        .hw13-eyebrow::before, .hw13-eyebrow::after {
          content:''; width: 18px; height: 1px; background: var(--accent);
        }
        .hw13-title {
          font-size: clamp(34px, 4.6vw, 60px);
          line-height: 1.0; letter-spacing: -0.04em;
          font-weight: 800; margin: 0;
          background: linear-gradient(135deg, #fff, #aaa);
          -webkit-background-clip: text; background-clip: text;
          -webkit-text-fill-color: transparent;
        }

        .hw13-stage {
          display: grid; grid-template-columns: 280px 1fr; gap: 32px;
          align-items: stretch;
        }

        /* LEFT: step tabs */
        .hw13-tabs {
          display: flex; flex-direction: column; gap: 10px;
        }
        .hw13-tab {
          padding: 18px 20px; border-radius: 16px;
          background: rgba(20,20,27,0.5);
          backdrop-filter: blur(10px);
          border: 1px solid rgba(255,255,255,0.06);
          cursor: pointer;
          transition: all .3s cubic-bezier(.2,.8,.2,1);
          position: relative; overflow: hidden;
          display: flex; flex-direction: column; gap: 6px;
        }
        .hw13-tab:hover { border-color: rgba(124,92,255,0.3); transform: translateX(3px); }
        .hw13-tab.on {
          background: linear-gradient(135deg, rgba(124,92,255,0.18), rgba(124,92,255,0.05));
          border-color: rgba(124,92,255,0.45);
          box-shadow: 0 0 0 1px rgba(124,92,255,0.2), 0 20px 40px -16px rgba(124,92,255,0.4);
        }
        .hw13-tab.on::before {
          content:''; position: absolute; left: 0; top: 0; bottom: 0; width: 3px;
          background: linear-gradient(180deg, #7c5cff, #c66dff);
        }
        .hw13-tab-num {
          font-family: 'JetBrains Mono', monospace;
          font-size: 11px; color: var(--muted-2);
          letter-spacing: 0.06em;
        }
        .hw13-tab.on .hw13-tab-num { color: var(--accent); }
        .hw13-tab-title {
          font-size: 17px; font-weight: 700; letter-spacing: -0.02em;
          color: #fff;
        }
        .hw13-tab-tag {
          font-family: 'JetBrains Mono', monospace; font-size: 10.5px;
          color: var(--muted); margin-top: 2px;
        }
        /* progress bar on active */
        .hw13-tab.on::after {
          content:''; position: absolute; left: 0; right: 0; bottom: 0; height: 2px;
          background: linear-gradient(90deg, #7c5cff, #c66dff);
          transform-origin: left;
          animation: hw13-prog 4.5s linear forwards;
        }
        @keyframes hw13-prog { from { transform: scaleX(0); } to { transform: scaleX(1); } }

        /* RIGHT: stage area */
        .hw13-screen {
          background: rgba(20,20,27,0.7);
          backdrop-filter: blur(20px);
          border: 1px solid rgba(255,255,255,0.08);
          border-radius: 22px;
          padding: 28px;
          position: relative; overflow: hidden;
          min-height: 420px;
          box-shadow: 0 40px 80px -30px rgba(0,0,0,0.6);
        }
        .hw13-screen::before {
          content:''; position: absolute; inset: 0;
          background:
            radial-gradient(circle at 30% 20%, rgba(124,92,255,0.15), transparent 50%),
            radial-gradient(circle at 70% 80%, rgba(236,76,184,0.10), transparent 50%);
          pointer-events: none;
        }
        .hw13-screen-inner { position: relative; height: 100%; min-height: 360px; }

        .hw13-controls {
          position: absolute; right: 18px; top: 18px;
          display: flex; align-items: center; gap: 10px;
          z-index: 3;
        }
        .hw13-auto {
          padding: 6px 12px 6px 8px; border-radius: 999px;
          background: rgba(76,217,164,0.12);
          border: 1px solid rgba(76,217,164,0.3);
          font-family: 'JetBrains Mono', monospace; font-size: 11px;
          color: #4cd9a4;
          display: inline-flex; align-items: center; gap: 6px;
          cursor: pointer;
        }
        .hw13-auto.off { background: rgba(255,255,255,0.04); border-color: rgba(255,255,255,0.08); color: var(--muted); }
        .hw13-auto .d { width: 6px; height: 6px; border-radius: 50%; background: currentColor; animation: pulse-dot 1.4s infinite; }

        /* STAGE 01 \u2014 prompt being typed */
        .hwst-prompt {
          background: rgba(8,8,12,0.6); border: 1px solid rgba(255,255,255,0.06);
          border-radius: 14px; padding: 18px 20px;
          font-family: 'JetBrains Mono', monospace; font-size: 13px;
          color: #fff;
        }
        .hwst-prompt .hint {
          font-size: 11px; color: var(--muted-2); margin-bottom: 8px;
          display: flex; align-items: center; gap: 6px;
        }
        .hwst-prompt .dot {
          width: 6px; height: 6px; border-radius: 50%;
          background: var(--accent);
          animation: pulse-dot 1.4s infinite;
        }
        .hwst-cur {
          display: inline-block; width: 2px; height: 14px;
          background: var(--accent); vertical-align: -2px; margin-left: 2px;
          animation: typing-cursor 0.7s steps(1) infinite;
        }
        .hwst-chips {
          display: flex; flex-wrap: wrap; gap: 6px; margin-top: 14px;
        }
        .hwst-chip {
          padding: 5px 11px; border-radius: 999px;
          background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.07);
          font-size: 12px; color: var(--muted);
          animation: float-up .4s ease both;
        }
        .hwst-chip:nth-child(1) { animation-delay: 0.3s; }
        .hwst-chip:nth-child(2) { animation-delay: 0.45s; }
        .hwst-chip:nth-child(3) { animation-delay: 0.6s; }
        .hwst-chip:nth-child(4) { animation-delay: 0.75s; }

        /* STAGE 02 \u2014 agent generating with file list */
        .hwst-files { display: flex; flex-direction: column; gap: 8px; }
        .hwst-file {
          padding: 11px 14px; border-radius: 10px;
          background: rgba(8,8,12,0.5); border: 1px solid rgba(255,255,255,0.06);
          display: flex; align-items: center; gap: 12px;
          font-family: 'JetBrains Mono', monospace; font-size: 12.5px;
          opacity: 0; transform: translateX(-12px);
          animation: hwst-slide .4s cubic-bezier(.2,.8,.2,1) forwards;
        }
        @keyframes hwst-slide { to { opacity: 1; transform: translateX(0); } }
        .hwst-file:nth-child(1) { animation-delay: 0.1s; }
        .hwst-file:nth-child(2) { animation-delay: 0.3s; }
        .hwst-file:nth-child(3) { animation-delay: 0.5s; }
        .hwst-file:nth-child(4) { animation-delay: 0.7s; }
        .hwst-file:nth-child(5) { animation-delay: 0.9s; }
        .hwst-file .ic {
          width: 22px; height: 22px; border-radius: 6px;
          background: var(--accent-soft); color: var(--accent);
          display: inline-flex; align-items: center; justify-content: center;
          font-size: 11px; flex-shrink: 0;
        }
        .hwst-file .nm { color: #fff; font-weight: 500; flex: 1; }
        .hwst-file .stat {
          font-size: 11px; color: var(--green); display: inline-flex; align-items: center; gap: 5px;
        }
        .hwst-file .stat .check { width: 4px; height: 4px; background: currentColor; border-radius: 50%; }
        .hwst-file.gen .stat { color: var(--accent); }
        .hwst-file.gen .stat .check { animation: spin 0.8s linear infinite; border: 1px solid currentColor; border-right-color: transparent; background: transparent; width: 8px; height: 8px; border-radius: 50%; }
        .hwst-meta {
          margin-top: 14px; padding: 12px 14px; border-radius: 10px;
          background: rgba(124,92,255,0.08); border: 1px solid rgba(124,92,255,0.2);
          display: flex; align-items: center; gap: 14px;
          font-family: 'JetBrains Mono', monospace; font-size: 11.5px;
        }
        .hwst-meta b { color: #fff; font-weight: 700; }
        .hwst-meta .sep { color: var(--muted-2); }

        /* STAGE 03 \u2014 versions timeline */
        .hwst-vers { display: flex; flex-direction: column; gap: 10px; padding-left: 20px; position: relative; }
        .hwst-vers::before {
          content:''; position: absolute; left: 5px; top: 12px; bottom: 12px; width: 2px;
          background: linear-gradient(180deg, var(--accent), rgba(124,92,255,0.2));
        }
        .hwst-v {
          padding: 12px 16px; border-radius: 12px;
          background: rgba(8,8,12,0.5); border: 1px solid rgba(255,255,255,0.06);
          position: relative;
          opacity: 0; transform: translateX(-10px);
          animation: hwst-slide .4s cubic-bezier(.2,.8,.2,1) forwards;
        }
        .hwst-v:nth-child(1) { animation-delay: 0.1s; }
        .hwst-v:nth-child(2) { animation-delay: 0.3s; }
        .hwst-v:nth-child(3) { animation-delay: 0.5s; }
        .hwst-v::before {
          content:''; position: absolute; left: -22px; top: 50%; transform: translateY(-50%);
          width: 12px; height: 12px; border-radius: 50%;
          background: var(--bg); border: 2px solid var(--muted-2);
        }
        .hwst-v.active::before { border-color: var(--accent); background: var(--accent); box-shadow: 0 0 0 4px rgba(124,92,255,0.2); }
        .hwst-v .row { display: flex; align-items: center; justify-content: space-between; }
        .hwst-v .ver { font-family: 'JetBrains Mono', monospace; font-size: 13px; font-weight: 700; color: #fff; }
        .hwst-v.active .ver { color: var(--accent); }
        .hwst-v .desc { font-size: 12.5px; color: var(--muted); margin-top: 4px; }
        .hwst-v .time { font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--muted-2); }
        .hwst-v .undo {
          padding: 4px 10px; border-radius: 6px; background: rgba(124,92,255,0.12); color: var(--accent);
          font-size: 11px; font-weight: 700;
        }

        /* STAGE 04 \u2014 deploy progress */
        .hwst-deploy {
          display: flex; flex-direction: column; gap: 16px;
        }
        .hwst-server {
          padding: 16px 18px; border-radius: 12px;
          background: rgba(8,8,12,0.5); border: 1px solid rgba(255,255,255,0.06);
          display: flex; align-items: center; gap: 14px;
        }
        .hwst-server .icon {
          width: 44px; height: 44px; border-radius: 11px;
          background: linear-gradient(135deg, var(--accent), #c66dff);
          display: inline-flex; align-items: center; justify-content: center;
          font-size: 18px; flex-shrink: 0;
        }
        .hwst-server .nm { color: #fff; font-weight: 700; font-size: 14px; font-family: 'JetBrains Mono', monospace; }
        .hwst-server .meta { color: var(--muted); font-size: 11.5px; margin-top: 2px; }
        .hwst-server .stat-pill {
          margin-left: auto; padding: 5px 11px 5px 9px; border-radius: 999px;
          background: rgba(76,217,164,0.12); border: 1px solid rgba(76,217,164,0.3);
          color: #4cd9a4; font-family: 'JetBrains Mono', monospace; font-size: 11px;
          display: inline-flex; align-items: center; gap: 5px;
        }
        .hwst-server .stat-pill .d { width: 6px; height: 6px; border-radius: 50%; background: #4cd9a4; animation: pulse-dot 1s infinite; }
        .hwst-prog {
          padding: 16px 18px; border-radius: 12px;
          background: rgba(8,8,12,0.5); border: 1px solid rgba(255,255,255,0.06);
        }
        .hwst-prog-label {
          display: flex; align-items: center; justify-content: space-between;
          font-family: 'JetBrains Mono', monospace; font-size: 12px; color: #fff;
          margin-bottom: 10px;
        }
        .hwst-prog-pct { color: var(--accent); font-weight: 700; }
        .hwst-prog-bar {
          height: 6px; border-radius: 999px; background: rgba(255,255,255,0.06); overflow: hidden;
        }
        .hwst-prog-fill {
          height: 100%; background: linear-gradient(90deg, var(--accent), #c66dff);
          border-radius: 999px;
          animation: hwst-fill 2.4s cubic-bezier(.5,0,.3,1) forwards;
        }
        @keyframes hwst-fill { from { width: 0%; } to { width: 100%; } }
        .hwst-prog-steps {
          display: flex; gap: 6px; margin-top: 12px; flex-wrap: wrap;
        }
        .hwst-prog-steps .s {
          padding: 4px 9px; border-radius: 6px;
          font-family: 'JetBrains Mono', monospace; font-size: 11px;
          background: rgba(76,217,164,0.10); color: #4cd9a4; border: 1px solid rgba(76,217,164,0.2);
          display: inline-flex; align-items: center; gap: 4px;
          opacity: 0; transform: translateY(4px);
          animation: hwst-slide .35s ease forwards;
        }
        .hwst-prog-steps .s:nth-child(1) { animation-delay: 0.3s; }
        .hwst-prog-steps .s:nth-child(2) { animation-delay: 0.7s; }
        .hwst-prog-steps .s:nth-child(3) { animation-delay: 1.2s; }
        .hwst-prog-steps .s:nth-child(4) { animation-delay: 1.8s; }
        .hwst-url {
          padding: 12px 16px; border-radius: 999px;
          background: linear-gradient(135deg, rgba(76,217,164,0.15), rgba(124,92,255,0.10));
          border: 1px solid rgba(76,217,164,0.3);
          display: inline-flex; align-items: center; gap: 9px;
          font-family: 'JetBrains Mono', monospace; font-size: 13px;
          opacity: 0; transform: translateY(8px);
          animation: hwst-slide .5s 2.2s cubic-bezier(.2,.8,.2,1) forwards;
        }
        .hwst-url .lock { width: 10px; height: 10px; border-radius: 50%; background: #4cd9a4; box-shadow: 0 0 8px #4cd9a4; }
        .hwst-url .scheme { color: #4cd9a4; font-weight: 700; }
        .hwst-url .host { color: #fff; font-weight: 600; }
        .hwst-url .arrow { color: var(--accent); font-weight: 700; margin-left: 6px; }

        @media (max-width: 880px) {
          .hw13-stage { grid-template-columns: 1fr; }
          .hw13-tabs { flex-direction: row; overflow-x: auto; padding-bottom: 4px; }
          .hw13-tab { min-width: 200px; }
        }
      `}),(0,r.jsxs)("div",{className:"hw13-inner",children:[(0,r.jsxs)("div",{className:"hw13-head",children:[(0,r.jsx)("div",{className:"hw13-eyebrow",children:"\u041A\u0430\u043A \u0440\u0430\u0431\u043E\u0442\u0430\u0435\u0442"}),(0,r.jsx)("h2",{className:"hw13-title",children:"\u041E\u0442 \u0438\u0434\u0435\u0438 \u0434\u043E \u0434\u043E\u043C\u0435\u043D\u0430 \u0437\u0430 \u043E\u0434\u0438\u043D \u0440\u0430\u0437\u0433\u043E\u0432\u043E\u0440"})]}),(0,r.jsxs)("div",{className:"hw13-stage",children:[(0,r.jsx)("div",{className:"hw13-tabs",children:nd.map((i,o)=>(0,r.jsxs)("div",{className:`hw13-tab ${o===e?"on":""}`,onClick:()=>{t(o),a(!1)},children:[(0,r.jsx)("div",{className:"hw13-tab-num",children:i.n}),(0,r.jsx)("div",{className:"hw13-tab-title",children:i.t}),(0,r.jsxs)("div",{className:"hw13-tab-tag",children:["// ",i.tag]})]},o))}),(0,r.jsxs)("div",{className:"hw13-screen",children:[(0,r.jsx)("div",{className:"hw13-controls",children:(0,r.jsxs)("div",{className:`hw13-auto ${n?"":"off"}`,onClick:()=>a(i=>!i),children:[(0,r.jsx)("span",{className:"d"}),n?"auto":"paused"]})}),(0,r.jsxs)("div",{className:"hw13-screen-inner",children:[e===0&&(0,r.jsx)(Rm,{}),e===1&&(0,r.jsx)(Lm,{}),e===2&&(0,r.jsx)(_m,{}),e===3&&(0,r.jsx)(Im,{})]},`stage-${e}`)]})]})]})]})}function Rm(){let[e,t]=I(""),n="\u0421\u0434\u0435\u043B\u0430\u0439 \u043B\u0435\u043D\u0434\u0438\u043D\u0433 \u0434\u043B\u044F \u043A\u043E\u0444\u0435\u0439\u043D\u0438 \xAB\u041F\u043E\u043B\u0451\u0442\xBB \u2014 \u0433\u0435\u0440\u043E\u0439, \u043C\u0435\u043D\u044E, \u043A\u043E\u043D\u0442\u0430\u043A\u0442\u044B, \u0442\u0451\u043F\u043B\u0430\u044F \u043F\u0430\u043B\u0438\u0442\u0440\u0430";return W(()=>{let a=0,i=()=>{a++,t(n.slice(0,a)),a<n.length&&setTimeout(i,35+Math.random()*30)};setTimeout(i,300)},[]),(0,r.jsxs)(r.Fragment,{children:[(0,r.jsxs)("div",{className:"hwst-prompt",children:[(0,r.jsxs)("div",{className:"hint",children:[(0,r.jsx)("span",{className:"dot"}),"\u043E\u043F\u0438\u0448\u0438\u0442\u0435 \u043F\u0440\u043E\u0435\u043A\u0442"]}),e,(0,r.jsx)("span",{className:"hwst-cur"})]}),(0,r.jsxs)("div",{className:"hwst-chips",children:[(0,r.jsx)("span",{className:"hwst-chip",children:"+ \u043B\u0435\u043D\u0434\u0438\u043D\u0433"}),(0,r.jsx)("span",{className:"hwst-chip",children:"+ \u043C\u0430\u0433\u0430\u0437\u0438\u043D"}),(0,r.jsx)("span",{className:"hwst-chip",children:"+ \u0434\u0430\u0448\u0431\u043E\u0440\u0434"}),(0,r.jsx)("span",{className:"hwst-chip",children:"+ CRM"})]})]})}function Lm(){return(0,r.jsxs)(r.Fragment,{children:[(0,r.jsx)("div",{className:"hwst-files",children:[{ic:"\u{1F4C4}",nm:"app/page.tsx",stat:"\u0433\u043E\u0442\u043E\u0432\u043E"},{ic:"\u{1F3A8}",nm:"components/Hero.tsx",stat:"\u0433\u043E\u0442\u043E\u0432\u043E"},{ic:"\u{1F3A8}",nm:"components/Menu.tsx",stat:"\u0433\u043E\u0442\u043E\u0432\u043E"},{ic:"\u2699",nm:"styles/theme.css",stat:"\u0433\u043E\u0442\u043E\u0432\u043E"},{ic:"\u{1F5C4}",nm:"lib/db.ts",stat:"\u0433\u0435\u043D\u0435\u0440\u0430\u0446\u0438\u044F"}].map((t,n)=>(0,r.jsxs)("div",{className:`hwst-file ${n===4?"gen":""}`,children:[(0,r.jsx)("span",{className:"ic",children:t.ic}),(0,r.jsx)("span",{className:"nm",children:t.nm}),(0,r.jsxs)("span",{className:"stat",children:[(0,r.jsx)("span",{className:"check"}),t.stat]})]},n))}),(0,r.jsxs)("div",{className:"hwst-meta",children:[(0,r.jsxs)("span",{children:[(0,r.jsx)("b",{children:"5"})," \u0444\u0430\u0439\u043B\u043E\u0432"]}),(0,r.jsx)("span",{className:"sep",children:"\xB7"}),(0,r.jsxs)("span",{children:[(0,r.jsx)("b",{children:"340"})," \u0441\u0442\u0440\u043E\u043A"]}),(0,r.jsx)("span",{className:"sep",children:"\xB7"}),(0,r.jsx)("span",{children:(0,r.jsx)("b",{children:"2.1 \u0441\u0435\u043A"})}),(0,r.jsx)("span",{className:"sep",children:"\xB7"}),(0,r.jsx)("span",{style:{color:"#4cd9a4"},children:"\u25CF \u0433\u0435\u043D\u0435\u0440\u0430\u0446\u0438\u044F"})]})]})}function _m(){return(0,r.jsx)("div",{className:"hwst-vers",children:[{v:"v1.3",d:"CTA \u043F\u0435\u0440\u0435\u043D\u0435\u0441\u0435\u043D\u0430 \u0432 hero, \u043E\u0441\u0432\u0435\u0442\u043B\u0451\u043D \u0444\u043E\u043D",t:"\u0441\u0435\u0439\u0447\u0430\u0441",active:!0},{v:"v1.2",d:"+ \u0444\u043E\u0440\u043C\u0430 \u0431\u0440\u043E\u043D\u0438, \u043C\u043E\u0431\u0438\u043B\u044C\u043D\u0430\u044F \u0432\u0451\u0440\u0441\u0442\u043A\u0430",t:"12:14"},{v:"v1.0",d:"\u041F\u0435\u0440\u0432\u0430\u044F \u0432\u0435\u0440\u0441\u0438\u044F \u043B\u0435\u043D\u0434\u0438\u043D\u0433\u0430",t:"11:02"}].map((t,n)=>(0,r.jsxs)("div",{className:`hwst-v ${t.active?"active":""}`,children:[(0,r.jsxs)("div",{className:"row",children:[(0,r.jsx)("span",{className:"ver",children:t.v}),t.active?(0,r.jsx)("span",{className:"time",children:"\u0441\u0435\u0439\u0447\u0430\u0441"}):(0,r.jsx)("span",{className:"undo",children:"\u21B6 \u043E\u0442\u043A\u0430\u0442\u0438\u0442\u044C \xB7 1 \u0441\u0435\u043A"})]}),(0,r.jsx)("div",{className:"desc",children:t.d})]},n))})}function Im(){return(0,r.jsxs)("div",{className:"hwst-deploy",children:[(0,r.jsxs)("div",{className:"hwst-server",children:[(0,r.jsx)("div",{className:"icon",children:"\u{1F5A5}"}),(0,r.jsxs)("div",{children:[(0,r.jsx)("div",{className:"nm",children:"ru-1.omnia.app"}),(0,r.jsx)("div",{className:"meta",children:"4 vCPU \xB7 8 GB \xB7 80 GB SSD \xB7 \u041C\u043E\u0441\u043A\u0432\u0430"})]}),(0,r.jsxs)("div",{className:"stat-pill",children:[(0,r.jsx)("span",{className:"d"}),"live"]})]}),(0,r.jsxs)("div",{className:"hwst-prog",children:[(0,r.jsxs)("div",{className:"hwst-prog-label",children:[(0,r.jsx)("span",{children:"\u0420\u0430\u0437\u0432\u0451\u0440\u0442\u044B\u0432\u0430\u043D\u0438\u0435"}),(0,r.jsx)("span",{className:"hwst-prog-pct",children:"100%"})]}),(0,r.jsx)("div",{className:"hwst-prog-bar",children:(0,r.jsx)("div",{className:"hwst-prog-fill"})}),(0,r.jsxs)("div",{className:"hwst-prog-steps",children:[(0,r.jsx)("span",{className:"s",children:"\u2713 \u0441\u0431\u043E\u0440\u043A\u0430"}),(0,r.jsx)("span",{className:"s",children:"\u2713 \u0444\u0430\u0439\u043B\u044B"}),(0,r.jsx)("span",{className:"s",children:"\u2713 \u0411\u0414"}),(0,r.jsx)("span",{className:"s",children:"\u2713 SSL"})]})]}),(0,r.jsxs)("div",{className:"hwst-url",children:[(0,r.jsx)("span",{className:"lock"}),(0,r.jsx)("span",{className:"scheme",children:"https://"}),(0,r.jsx)("span",{className:"host",children:"cafe-polet.omnia.app"}),(0,r.jsx)("span",{className:"arrow",children:"\u043E\u0442\u043A\u0440\u044B\u0442\u044C \u2192"})]})]})}var ds=[{kind:"visitka",n:"\u0421\u0430\u0439\u0442-\u0432\u0438\u0437\u0438\u0442\u043A\u0430",short:"\u0412\u0438\u0437\u0438\u0442\u043A\u0430",icon:"\u2726",accent:"#7c5cff",prompt:"\u0411\u0430\u0440\u0431\u0435\u0440\u0448\u043E\u043F \u0432 \u041A\u0430\u0437\u0430\u043D\u0438 \u2014 \u0443\u0441\u043B\u0443\u0433\u0438, \u0446\u0435\u043D\u044B, \u043E\u043D\u043B\u0430\u0439\u043D-\u0437\u0430\u043F\u0438\u0441\u044C",result:"\u041B\u0435\u043D\u0434\u0438\u043D\u0433 \u043D\u0430 \u0441\u0432\u043E\u0451\u043C \u0434\u043E\u043C\u0435\u043D\u0435 + \u0444\u043E\u0440\u043C\u0430 \u0437\u0430\u043F\u0438\u0441\u0438 + Telegram-\u0431\u043E\u0442 \u043D\u0430\u043F\u043E\u043C\u0438\u043D\u0430\u043D\u0438\u0439",domain:"razor-kazan.omnia.app",competitor:"3 \u043F\u043E\u0434\u043F\u0438\u0441\u043A\u0438 + \u0440\u0430\u0437\u0440\u0430\u0431\u043E\u0442\u0447\u0438\u043A \xB7 \u0445\u0440\u0430\u043D\u0435\u043D\u0438\u0435 \u0437\u0430 \u0433\u0440\u0430\u043D\u0438\u0446\u0435\u0439",oldPrice:"100\u2013200 \u0442\u044B\u0441 \u20BD",oldTime:"2\u20133 \u043D\u0435\u0434\u0435\u043B\u0438",neuPrice:"\u043E\u0442 990 \u20BD/\u043C\u0435\u0441",neuTime:"5\u201315 \u043C\u0438\u043D",saved:"500\xD7",savedBudget:"99%",pains:["\u041D\u0435\u0441\u043A\u043E\u043B\u044C\u043A\u043E \u043F\u043E\u0434\u043F\u0438\u0441\u043E\u043A \xB7 \u043E\u0434\u043D\u0430 \u043D\u0435 \u0434\u0440\u0443\u0436\u0438\u0442 \u0441 \u0434\u0440\u0443\u0433\u043E\u0439","\u0421\u0440\u044B\u0432 \u0441\u0440\u043E\u043A\u043E\u0432 \u0443 \u0444\u0440\u0438\u043B\u0430\u043D\u0441\u0435\u0440\u0430","\u0414\u0430\u043D\u043D\u044B\u0435 \u043A\u043B\u0438\u0435\u043D\u0442\u043E\u0432 \u0445\u0440\u0430\u043D\u044F\u0442\u0441\u044F \u0437\u0430 \u0433\u0440\u0430\u043D\u0438\u0446\u0435\u0439"],gains:["\u041E\u0434\u043D\u0430 \u043F\u043E\u0434\u043F\u0438\u0441\u043A\u0430 \xB7 \u0432\u0441\u0451 \u0432 \u043E\u0434\u043D\u043E\u043C","\u0413\u043E\u0442\u043E\u0432\u043E \u0437\u0430 15 \u043C\u0438\u043D \xB7 \u0431\u0435\u0437 \u043F\u043E\u0434\u0440\u044F\u0434\u0447\u0438\u043A\u0430","\u0421\u0435\u0440\u0432\u0435\u0440\u044B \u0432 \u041C\u043E\u0441\u043A\u0432\u0435 \xB7 152-\u0424\u0417"],tags:["\u043E\u043D\u043B\u0430\u0439\u043D-\u0437\u0430\u043F\u0438\u0441\u044C","Telegram-\u0431\u043E\u0442",".ru-\u0434\u043E\u043C\u0435\u043D","152-\u0424\u0417"],kpis:[{k:"8/\u043C\u0435\u0441",v:"\u043F\u0440\u0430\u0432\u043E\u043A \u0442\u0435\u043A\u0441\u0442\u043E\u043C"},{k:"4.9 \u2605",v:"\u043E\u0442\u0437\u044B\u0432\u044B \u043A\u043B\u0438\u0435\u043D\u0442\u043E\u0432"}],tier:"Lite"},{kind:"shop",n:"\u0418\u043D\u0442\u0435\u0440\u043D\u0435\u0442-\u043C\u0430\u0433\u0430\u0437\u0438\u043D",short:"\u041C\u0430\u0433\u0430\u0437\u0438\u043D",icon:"\u{1F6CD}",accent:"#0ea5e9",prompt:"\u0418\u041C \u0436\u0435\u043D\u0441\u043A\u043E\u0439 \u043E\u0434\u0435\u0436\u0434\u044B \u2014 \u043A\u0430\u0442\u0430\u043B\u043E\u0433, \u043E\u043F\u043B\u0430\u0442\u0430 \u0421\u0411\u041F, \u0434\u043E\u0441\u0442\u0430\u0432\u043A\u0430 \u0421\u0414\u042D\u041A",result:"\u041C\u0430\u0433\u0430\u0437\u0438\u043D \u0441 \u043F\u0440\u0438\u0451\u043C\u043E\u043C \u043F\u043B\u0430\u0442\u0435\u0436\u0435\u0439, \u0438\u043D\u0442\u0435\u0433\u0440\u0430\u0446\u0438\u0435\u0439 \u0421\u0414\u042D\u041A \u0438 \u0430\u0434\u043C\u0438\u043D\u043A\u043E\u0439",domain:"pole-store.omnia.app",competitor:"\u041F\u043B\u0430\u0442\u0444\u043E\u0440\u043C\u0430 + \u043A\u0430\u0441\u0442\u043E\u043C\u0438\u0437\u0430\u0446\u0438\u044F + \u0440\u0430\u0437\u0440\u0430\u0431\u043E\u0442\u0447\u0438\u043A",oldPrice:"200\u2013500 \u0442\u044B\u0441 \u20BD",oldTime:"1\u20132 \u043C\u0435\u0441\u044F\u0446\u0430",neuPrice:"\u043E\u0442 2 490 \u20BD/\u043C\u0435\u0441",neuTime:"40 \u043C\u0438\u043D",saved:"40\xD7",savedBudget:"99%",pains:["\u0421\u0414\u042D\u041A \u043F\u043E\u0434\u043A\u043B\u044E\u0447\u0430\u0435\u0442\u0441\u044F \u043E\u0442\u0434\u0435\u043B\u044C\u043D\u043E","\u0410\u0434\u043C\u0438\u043D\u043A\u0430 \u0442\u043E\u043B\u044C\u043A\u043E \u0437\u0430 \u043A\u0430\u0441\u0442\u043E\u043C\u043A\u0443","\u041F\u043E\u0434\u0434\u0435\u0440\u0436\u043A\u0430 5\u201315 \u0442\u044B\u0441/\u043C\u0435\u0441 \u0441\u0432\u0435\u0440\u0445\u0443"],gains:["\u0421\u0414\u042D\u041A + \u0421\u0411\u041F \u0438\u0437 \u043A\u043E\u0440\u043E\u0431\u043A\u0438","\u0410\u0434\u043C\u0438\u043D\u043A\u0430 \u0433\u043E\u0442\u043E\u0432\u0430","\u041F\u043E\u0434\u0434\u0435\u0440\u0436\u043A\u0430 \u0432\u043A\u043B\u044E\u0447\u0435\u043D\u0430 \u0432 \u0442\u0430\u0440\u0438\u0444"],tags:["\u043A\u0430\u0442\u0430\u043B\u043E\u0433","\u0421\u0411\u041F","\u0421\u0414\u042D\u041A","\u0430\u0434\u043C\u0438\u043D\u043A\u0430"],kpis:[{k:"127",v:"\u0442\u043E\u0432\u0430\u0440\u043E\u0432 \u0432 \u043A\u0430\u0442\u0430\u043B\u043E\u0433\u0435"},{k:"~3 \u0441\u0435\u043A",v:"\u0432\u044B\u0431\u043E\u0440 \u2192 \u043E\u043F\u043B\u0430\u0442\u0430"}],tier:"Solo"},{kind:"chat",n:"\u041C\u0435\u0441\u0441\u0435\u043D\u0434\u0436\u0435\u0440 \u0434\u043B\u044F \u0441\u043E\u043E\u0431\u0449\u0435\u0441\u0442\u0432\u0430",short:"\u0427\u0430\u0442 \u0448\u043A\u043E\u043B\u044B",icon:"\u{1F4AC}",accent:"#10b981",prompt:"\u0417\u0430\u043A\u0440\u044B\u0442\u044B\u0439 \u0447\u0430\u0442 \u0434\u043B\u044F \u0443\u0447\u0435\u043D\u0438\u043A\u043E\u0432 \u043E\u043D\u043B\u0430\u0439\u043D-\u0448\u043A\u043E\u043B\u044B \u2014 \u043A\u043E\u043C\u043D\u0430\u0442\u044B, \u0433\u043E\u043B\u043E\u0441\u043E\u0432\u044B\u0435, \u0444\u0430\u0439\u043B\u044B",result:"\u0412\u0435\u0431-\u043C\u0435\u0441\u0441\u0435\u043D\u0434\u0436\u0435\u0440 \u0441 \u0440\u0435\u0433\u0438\u0441\u0442\u0440\u0430\u0446\u0438\u0435\u0439, \u043A\u043E\u043C\u043D\u0430\u0442\u0430\u043C\u0438, \u043C\u0435\u0434\u0438\u0430 \u2014 \u043D\u0430 \u0441\u0432\u043E\u0451\u043C \u0434\u043E\u043C\u0435\u043D\u0435",domain:"lyceum7.omnia.app",competitor:"\u0427\u0443\u0436\u0438\u0435 \u043C\u0435\u0441\u0441\u0435\u043D\u0434\u0436\u0435\u0440\u044B + \u043A\u0430\u0441\u0442\u043E\u043C\u043D\u0430\u044F \u0440\u0430\u0437\u0440\u0430\u0431\u043E\u0442\u043A\u0430",oldPrice:"500 \u0442\u044B\u0441 \u2013 2 \u043C\u043B\u043D \u20BD",oldTime:"3\u20136 \u043C\u0435\u0441\u044F\u0446\u0435\u0432",neuPrice:"\u043E\u0442 8 990 \u20BD/\u043C\u0435\u0441",neuTime:"1 \u0434\u0435\u043D\u044C",saved:"90\xD7",savedBudget:"99.5%",pains:["\u0427\u0443\u0436\u0430\u044F \u043F\u043B\u0430\u0442\u0444\u043E\u0440\u043C\u0430 \xB7 \u043D\u0435\u0442 \u0441\u0432\u043E\u0435\u0433\u043E \u0431\u0440\u0435\u043D\u0434\u0430","\u041D\u0435\u0442 \u043A\u043E\u043D\u0442\u0440\u043E\u043B\u044F \u043D\u0430\u0434 \u0434\u0430\u043D\u043D\u044B\u043C\u0438","UI \u0437\u0430\u0432\u0438\u0441\u0438\u0442 \u043E\u0442 \u0447\u0443\u0436\u043E\u0433\u043E \u043F\u0440\u043E\u0434\u0443\u043A\u0442\u0430"],gains:["\u0421\u0432\u043E\u0439 \u0431\u0440\u0435\u043D\u0434 + \u0434\u043E\u043C\u0435\u043D","\u041F\u043E\u043B\u043D\u044B\u0439 \u043A\u043E\u043D\u0442\u0440\u043E\u043B\u044C \xB7 152-\u0424\u0417","\u041A\u0430\u0441\u0442\u043E\u043C\u043D\u044B\u0439 \u0440\u0443\u0441\u0441\u043A\u0438\u0439 UI"],tags:["\u043A\u043E\u043C\u043D\u0430\u0442\u044B","\u0433\u043E\u043B\u043E\u0441\u043E\u0432\u044B\u0435","\u0444\u0430\u0439\u043B\u044B","\u0440\u043E\u043B\u0438"],kpis:[{k:"247",v:"\u0443\u0447\u0435\u043D\u0438\u043A\u043E\u0432 \u0432 \u0447\u0430\u0442\u0435"},{k:"12",v:"\u0430\u043A\u0442\u0438\u0432\u043D\u044B\u0445 \u043A\u0430\u043D\u0430\u043B\u043E\u0432"}],tier:"Pro"},{kind:"bot",n:"\u0418\u0418-\u0430\u0433\u0435\u043D\u0442 \u0441 RAG",short:"AI-\u0431\u043E\u0442",icon:"\u{1F9E0}",accent:"#ec4cb8",prompt:"Telegram-\u0431\u043E\u0442, \u043E\u0442\u0432\u0435\u0447\u0430\u0435\u0442 \u043A\u043B\u0438\u0435\u043D\u0442\u0430\u043C \u043F\u043E \u043C\u043E\u0435\u0439 \u0431\u0430\u0437\u0435 \u0437\u043D\u0430\u043D\u0438\u0439 24/7",result:"\u0411\u043E\u0442 \u0441 RAG-\u043F\u043E\u0438\u0441\u043A\u043E\u043C \u043F\u043E PDF/Word, \u043C\u0435\u0442\u0440\u0438\u043A\u0438, \u043F\u0435\u0440\u0435\u0434\u0430\u0447\u0430 \u043E\u043F\u0435\u0440\u0430\u0442\u043E\u0440\u0443",domain:"knowledge.omnia.app",competitor:"No-code \u043A\u043E\u043D\u0441\u0442\u0440\u0443\u043A\u0442\u043E\u0440 + GPT-\u043E\u0431\u0432\u044F\u0437\u043A\u0430 + \u0440\u0430\u0437\u0440\u0430\u0431\u043E\u0442\u0447\u0438\u043A",oldPrice:"300\u2013800 \u0442\u044B\u0441 \u20BD",oldTime:"2\u20134 \u043D\u0435\u0434\u0435\u043B\u0438",neuPrice:"\u043E\u0442 8 990 \u20BD/\u043C\u0435\u0441",neuTime:"20 \u043C\u0438\u043D",saved:"60\xD7",savedBudget:"98%",pains:["No-code \u043A\u043E\u043D\u0441\u0442\u0440\u0443\u043A\u0442\u043E\u0440\u044B \u043D\u0435 \u0443\u043C\u0435\u044E\u0442 RAG","OpenAI \u0440\u0435\u0436\u0435\u0442\u0441\u044F \u0432 \u0420\u0424","\u041D\u0435\u0442 \u043B\u043E\u0433\u043E\u0432 \u0438 \u0430\u043D\u0430\u043B\u0438\u0442\u0438\u043A\u0438"],gains:["RAG \u0438\u0437 \u043A\u043E\u0440\u043E\u0431\u043A\u0438 (Word/PDF)","YandexGPT \u0438 GigaChat \u043F\u043E \u0443\u043C\u043E\u043B\u0447.","\u0414\u0430\u0448\u0431\u043E\u0440\u0434 \u0441 \u043C\u0435\u0442\u0440\u0438\u043A\u0430\u043C\u0438"],tags:["RAG-\u043F\u043E\u0438\u0441\u043A","PDF / Word","Telegram","\u0434\u0430\u0448\u0431\u043E\u0440\u0434"],kpis:[{k:"1 247",v:"\u043E\u0442\u0432\u0435\u0442\u043E\u0432 \u0432 \u0434\u0435\u043D\u044C"},{k:"94%",v:"\u0442\u043E\u0447\u043D\u043E\u0441\u0442\u044C RAG"}],tier:"Pro"},{kind:"saas",n:"SaaS / \u041B\u0438\u0447\u043D\u044B\u0439 \u043A\u0430\u0431\u0438\u043D\u0435\u0442",short:"\u041A\u0430\u0431\u0438\u043D\u0435\u0442",icon:"\u26A1",accent:"#f97316",prompt:"\u041A\u0430\u0431\u0438\u043D\u0435\u0442 \u0434\u043B\u044F \u043A\u043B\u0438\u0435\u043D\u0442\u043E\u0432 \u2014 \u0441\u0442\u0430\u0442\u0443\u0441 \u0437\u0430\u043A\u0430\u0437\u043E\u0432, \u043E\u043F\u043B\u0430\u0442\u044B, \u0434\u043E\u043A\u0443\u043C\u0435\u043D\u0442\u044B",result:"\u0412\u0435\u0431-\u043A\u0430\u0431\u0438\u043D\u0435\u0442 \u0441 \u0430\u0432\u0442\u043E\u0440\u0438\u0437\u0430\u0446\u0438\u0435\u0439, \u0440\u043E\u043B\u044F\u043C\u0438, \u0411\u0414 \u2014 \u043D\u0430 \u0441\u0432\u043E\u0451\u043C \u0434\u043E\u043C\u0435\u043D\u0435",domain:"cabinet.omnia.app",competitor:"Backend-\u0444\u0440\u0435\u0439\u043C\u0432\u043E\u0440\u043A + \u043A\u0430\u0441\u0442\u043E\u043C\u043D\u0430\u044F \u0440\u0430\u0437\u0440\u0430\u0431\u043E\u0442\u043A\u0430",oldPrice:"500 \u0442\u044B\u0441 \u2013 1.5 \u043C\u043B\u043D \u20BD",oldTime:"2\u20133 \u043C\u0435\u0441\u044F\u0446\u0430",neuPrice:"\u043E\u0442 8 990 \u20BD/\u043C\u0435\u0441",neuTime:"1 \u0447\u0430\u0441",saved:"60\xD7",savedBudget:"98%",pains:["\u041F\u043E\u043B\u0433\u043E\u0434\u0430 \u043D\u0430 \u0440\u0430\u0437\u0440\u0430\u0431\u043E\u0442\u043A\u0443 \u0441 \u043D\u0443\u043B\u044F","\u041D\u0430\u0439\u043C backend-\u0440\u0430\u0437\u0440\u0430\u0431\u043E\u0442\u0447\u0438\u043A\u0430","\u041F\u043E\u0434\u0434\u0435\u0440\u0436\u043A\u0430 \u043E\u0442 30 \u0442\u044B\u0441/\u043C\u0435\u0441"],gains:["\u041A\u0430\u0431\u0438\u043D\u0435\u0442 \u0437\u0430 1 \u0447\u0430\u0441","\u0411\u0435\u0437 \u043F\u0440\u043E\u0433\u0440\u0430\u043C\u043C\u0438\u0441\u0442\u043E\u0432","\u041F\u043E\u0434\u0434\u0435\u0440\u0436\u043A\u0430 \u0432\u043A\u043B\u044E\u0447\u0435\u043D\u0430 \u0432 \u043F\u043E\u0434\u043F\u0438\u0441\u043A\u0443"],tags:["\u0430\u0432\u0442\u043E\u0440\u0438\u0437\u0430\u0446\u0438\u044F","\u0440\u043E\u043B\u0438","\u0431\u0430\u0437\u0430 \u0434\u0430\u043D\u043D\u044B\u0445","admin-API"],kpis:[{k:"420",v:"\u0430\u043A\u0442\u0438\u0432\u043D\u044B\u0445 \u043A\u043B\u0438\u0435\u043D\u0442\u043E\u0432"},{k:"~\u20BD284k",v:"\u043E\u0431\u043E\u0440\u043E\u0442 \u0437\u0430 \u043C\u0435\u0441\u044F\u0446"}],tier:"Pro"}];function $m(){let[e,t]=I(0),[n,a]=I(!0);W(()=>{if(!n)return;let o=setTimeout(()=>t(s=>(s+1)%ds.length),5e3);return()=>clearTimeout(o)},[e,n]);let i=ds[e];return(0,r.jsxs)("section",{id:"cases",className:"uc13",children:[(0,r.jsx)("style",{children:`
        .uc13 {
          padding: 100px 24px;
          position: relative;
        }
        .uc13-inner { max-width: 1240px; margin: 0 auto; }
        .uc13-head { text-align: center; max-width: 760px; margin: 0 auto 56px; }
        .uc13-eyebrow {
          font-size: 12px; letter-spacing: 0.14em; text-transform: uppercase;
          color: var(--accent); font-weight: 700;
          display: inline-flex; align-items: center; gap: 10px; margin-bottom: 14px;
        }
        .uc13-eyebrow::before, .uc13-eyebrow::after {
          content:''; width: 18px; height: 1px; background: var(--accent);
        }
        .uc13-title {
          font-size: clamp(34px, 4.6vw, 60px);
          line-height: 1.0; letter-spacing: -0.04em;
          font-weight: 800; margin: 0 0 14px;
          background: linear-gradient(135deg, #fff, #aaa);
          -webkit-background-clip: text; background-clip: text;
          -webkit-text-fill-color: transparent;
        }
        .uc13-sub {
          font-size: 17px; color: var(--muted); line-height: 1.5;
        }

        .uc13-stage {
          display: grid; grid-template-columns: 320px 1fr; gap: 32px;
          align-items: start;
        }

        /* LEFT: list */
        .uc13-list {
          display: flex; flex-direction: column; gap: 6px;
          position: sticky; top: 100px;
        }
        .uc13-item {
          padding: 16px 18px; border-radius: 14px;
          background: rgba(20,20,27,0.5); backdrop-filter: blur(10px);
          border: 1px solid rgba(255,255,255,0.06);
          display: flex; align-items: center; gap: 14px;
          cursor: pointer;
          transition: all .3s cubic-bezier(.2,.8,.2,1);
          position: relative; overflow: hidden;
        }
        .uc13-item:hover { border-color: rgba(124,92,255,0.3); transform: translateX(4px); }
        .uc13-item.on {
          background: linear-gradient(135deg, var(--it-accent, rgba(124,92,255,0.2)), rgba(20,20,27,0.6));
          border-color: var(--it-border, rgba(124,92,255,0.5));
        }
        .uc13-item.on::after {
          content:''; position: absolute; left: 0; right: 0; bottom: 0; height: 2px;
          background: var(--it-color, var(--accent));
          animation: hw13-prog 5s linear forwards;
          transform-origin: left;
        }
        .uc13-item .ic {
          width: 36px; height: 36px; border-radius: 10px;
          background: var(--it-accent, rgba(124,92,255,0.15));
          display: inline-flex; align-items: center; justify-content: center;
          font-size: 17px; flex-shrink: 0;
        }
        .uc13-item .nm {
          font-size: 15px; font-weight: 700; color: #fff;
          flex: 1; min-width: 0;
        }
        .uc13-item .arr {
          color: var(--it-color, var(--accent));
          opacity: 0; transform: translateX(-4px);
          transition: all .25s;
        }
        .uc13-item.on .arr { opacity: 1; transform: translateX(0); }

        /* RIGHT: showcase */
        .uc13-show {
          background: rgba(20,20,27,0.7); backdrop-filter: blur(20px);
          border: 1px solid rgba(255,255,255,0.08);
          border-radius: 22px;
          padding: 0;
          overflow: hidden;
          position: relative;
          box-shadow: 0 40px 80px -30px rgba(0,0,0,0.6);
        }
        .uc13-show::before {
          content:''; position: absolute; inset: 0;
          background: var(--it-bg);
          opacity: 0.5;
          pointer-events: none;
        }
        .uc13-show-inner {
          position: relative; padding: 28px 30px;
          display: flex; flex-direction: column; gap: 20px;
        }
        /* TOP \u2014 header strip with title + tags */
        .uc13-show-top {
          display: grid; grid-template-columns: 1fr auto; gap: 18px;
          align-items: start;
          padding-bottom: 18px;
          border-bottom: 1px solid rgba(255,255,255,0.07);
        }
        .uc13-show-top-left {
          display: flex; gap: 14px; align-items: flex-start;
          min-width: 0;
        }
        .uc13-show-top .ic {
          width: 48px; height: 48px; border-radius: 14px;
          background: linear-gradient(135deg, var(--it-color), color-mix(in srgb, var(--it-color), white 30%));
          display: inline-flex; align-items: center; justify-content: center;
          font-size: 22px; box-shadow: 0 12px 28px -8px var(--it-color);
          flex-shrink: 0;
        }
        .uc13-show-cat {
          font-size: 13px; color: var(--it-color); font-weight: 800;
          text-transform: uppercase; letter-spacing: 0.1em;
          display: flex; align-items: center; gap: 8px;
          margin-bottom: 4px;
        }
        .uc13-tier-pill {
          font-family: 'JetBrains Mono', monospace; font-size: 10px;
          padding: 2px 7px; border-radius: 4px;
          background: rgba(255,255,255,0.08); color: rgba(255,255,255,0.7);
          font-weight: 700; letter-spacing: 0.04em;
        }
        .uc13-show-name {
          font-size: 22px; font-weight: 800; color: #fff; letter-spacing: -0.025em; line-height: 1.2;
        }
        .uc13-show-tags {
          display: flex; flex-wrap: wrap; gap: 6px;
          align-items: flex-start;
          max-width: 280px;
          justify-content: flex-end;
        }
        .uc13-tag {
          padding: 4px 10px; border-radius: 999px;
          background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.08);
          font-family: 'JetBrains Mono', monospace; font-size: 11px;
          color: rgba(255,255,255,0.7); font-weight: 500;
          white-space: nowrap;
        }

        /* DEVICE \u2014 browser frame with stage */
        .uc13-device {
          background: linear-gradient(135deg, #1f2228, #14171d);
          border: 1px solid rgba(255,255,255,0.08);
          border-radius: 14px;
          overflow: hidden;
          box-shadow: 0 30px 70px -20px rgba(0,0,0,0.6),
                      0 0 0 1px rgba(255,255,255,0.03) inset;
        }
        .uc13-device-chrome {
          background: linear-gradient(180deg, #2a2d35, #1f2228);
          border-bottom: 1px solid rgba(0,0,0,0.4);
          padding: 9px 14px;
          display: flex; align-items: center; gap: 12px;
        }
        .uc13-device-dots { display: flex; gap: 6px; }
        .uc13-device-dots span {
          width: 11px; height: 11px; border-radius: 50%;
          background: #3a3d45;
        }
        .uc13-device-dots span:nth-child(1) { background: #ff5f57; }
        .uc13-device-dots span:nth-child(2) { background: #ffbd2e; }
        .uc13-device-dots span:nth-child(3) { background: #28c840; }
        .uc13-device-url {
          flex: 1;
          padding: 5px 12px; border-radius: 6px;
          background: rgba(0,0,0,0.35);
          display: flex; align-items: center; gap: 8px;
          font-family: 'JetBrains Mono', monospace; font-size: 11.5px;
          color: rgba(255,255,255,0.6);
        }
        .uc13-device-url .lock { font-size: 9px; }
        .uc13-device-url .u-scheme { color: var(--it-color); font-weight: 600; }
        .uc13-device-url .u-host { color: #fff; font-weight: 600; }
        .uc13-device-actions {
          display: flex; align-items: center;
        }
        .uc13-device-actions .badge {
          padding: 3px 9px 3px 7px; border-radius: 999px;
          background: rgba(76,217,164,0.15); color: #4cd9a4;
          font-family: 'JetBrains Mono', monospace; font-size: 10.5px;
          font-weight: 700; letter-spacing: 0.04em;
          display: inline-flex; align-items: center; gap: 5px;
        }
        .uc13-device-actions .badge::before {
          content:''; width: 5px; height: 5px; border-radius: 50%;
          background: #4cd9a4; box-shadow: 0 0 6px #4cd9a4;
          animation: pulse-dot 1.4s infinite;
        }
        .uc13-device-actions {
          display: flex; align-items: center; gap: 10px;
        }
        /* v1/v2/v3 tabs in browser chrome */
        .uc13-tabs {
          display: flex; gap: 2px;
          padding: 2px;
          background: rgba(0,0,0,0.3);
          border-radius: 5px;
        }
        .uc13-tabv {
          padding: 3px 9px;
          font-family: 'JetBrains Mono', monospace;
          font-size: 10.5px; font-weight: 700;
          color: rgba(255,255,255,0.5);
          background: transparent; border: 0;
          border-radius: 3px;
          cursor: pointer;
          transition: all .2s;
        }
        .uc13-tabv:hover { color: rgba(255,255,255,0.85); }
        .uc13-tabv.on {
          background: var(--it-color);
          color: #fff;
          box-shadow: 0 4px 8px -2px var(--it-color);
        }
        /* device stage \u2014 single big mockup scaled */
        .uc13-device-stage {
          padding: 30px;
          background:
            radial-gradient(ellipse at 20% 20%, color-mix(in srgb, var(--it-color), #000 60%), transparent 60%),
            radial-gradient(ellipse at 80% 80%, color-mix(in srgb, var(--it-color), #000 70%), transparent 60%),
            #0a0c12;
          min-height: 420px;
          position: relative;
          display: flex; align-items: center; justify-content: center;
        }
        .uc13-device-stage::after {
          content:''; position: absolute; inset: 0;
          background-image:
            linear-gradient(rgba(255,255,255,0.02) 1px, transparent 1px),
            linear-gradient(90deg, rgba(255,255,255,0.02) 1px, transparent 1px);
          background-size: 32px 32px;
          mask-image: radial-gradient(ellipse at center, black 30%, transparent 80%);
          pointer-events: none;
        }
        /* the actual mockup card \u2014 large + scaled content via zoom */
        .uc13-device-zoom {
          position: relative; z-index: 2;
          width: 100%;
          max-width: 560px;
          aspect-ratio: 4/3.6;
          background: var(--it-grad);
          border-radius: 14px;
          padding: 16px;
          box-shadow:
            0 30px 70px -20px rgba(0,0,0,0.7),
            0 0 0 2px var(--it-color),
            0 0 60px var(--it-color)40,
            inset 0 1px 0 rgba(255,255,255,0.15);
          animation: card-pop-in 0.5s cubic-bezier(.2,1,.3,1) both;
        }
        /* SCALE the .mc-page content inside \u2014 fonts/icons become readable */
        .uc13-device-zoom .mc-page {
          zoom: 3.2;
        }
        /* Firefox fallback (no zoom support) \u2014 use transform scale */
        @-moz-document url-prefix() {
          .uc13-device-zoom .mc-page {
            zoom: 1;
            transform: scale(3.2);
            transform-origin: top left;
            width: 31.25%;
            height: 31.25%;
          }
        }

        /* KPI strip */
        .uc13-kpis {
          display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 10px;
        }
        .uc13-kpi {
          padding: 12px 14px; border-radius: 12px;
          background: rgba(8,8,12,0.55); border: 1px solid rgba(255,255,255,0.06);
          display: flex; flex-direction: column; gap: 3px;
        }
        .uc13-kpi.accent {
          background: linear-gradient(135deg, var(--it-color)20, rgba(8,8,12,0.5));
          border-color: var(--it-color);
        }
        .uc13-kpi .kv {
          font-size: 18px; font-weight: 900; color: #fff;
          letter-spacing: -0.02em; line-height: 1;
          font-variant-numeric: tabular-nums;
        }
        .uc13-kpi.accent .kv {
          background: linear-gradient(135deg, var(--it-color), color-mix(in srgb, var(--it-color), white 30%));
          -webkit-background-clip: text; background-clip: text;
          -webkit-text-fill-color: transparent;
        }
        .uc13-kpi .kl {
          font-size: 11px; color: var(--muted);
          font-family: 'JetBrains Mono', monospace;
        }

        /* VS comparison \u2014 pain/gain rich panels */
        .uc13-vs {
          display: grid; grid-template-columns: 1fr 36px 1fr; gap: 14px;
          align-items: stretch;
        }
        .uc13-side {
          padding: 16px 18px; border-radius: 14px;
          background: rgba(8,8,12,0.55); border: 1px solid rgba(255,255,255,0.06);
          display: flex; flex-direction: column; gap: 12px;
        }
        .uc13-side.uc13-neu {
          background: linear-gradient(135deg, var(--it-color)18, rgba(8,8,12,0.6));
          border-color: var(--it-color);
          box-shadow: 0 12px 30px -10px var(--it-color);
        }
        .uc13-side-head {
          display: flex; flex-direction: column; gap: 4px;
        }
        .uc13-side-lbl {
          font-family: 'JetBrains Mono', monospace; font-size: 10.5px;
          text-transform: uppercase; letter-spacing: 0.1em;
          font-weight: 800;
        }
        .uc13-old .uc13-side-lbl { color: #ef4444; }
        .uc13-neu .uc13-side-lbl { color: var(--it-color); }
        .uc13-side-cmp {
          font-size: 13px; color: rgba(255,255,255,0.9); font-weight: 700;
          line-height: 1.25;
        }
        .uc13-side-list {
          list-style: none; padding: 0; margin: 0;
          display: flex; flex-direction: column; gap: 7px;
        }
        .uc13-side-list li {
          font-size: 12.5px; color: rgba(255,255,255,0.75); line-height: 1.35;
          display: flex; gap: 8px; align-items: flex-start;
        }
        .uc13-side-list li .mark {
          width: 14px; height: 14px; border-radius: 50%;
          display: inline-flex; align-items: center; justify-content: center;
          font-size: 9px; font-weight: 900;
          flex-shrink: 0; margin-top: 1px;
        }
        .uc13-old .uc13-side-list li .mark { background: rgba(239,68,68,0.15); color: #ef4444; }
        .uc13-neu .uc13-side-list li .mark { background: var(--it-color); color: #fff; }
        .uc13-side-foot {
          margin-top: auto;
          padding-top: 10px;
          border-top: 1px dashed rgba(255,255,255,0.1);
          display: flex; align-items: center; gap: 12px;
          font-family: 'JetBrains Mono', monospace;
        }
        .uc13-side-foot .t {
          font-size: 12px; color: var(--muted); font-weight: 600;
        }
        .uc13-side-foot .p {
          font-size: 14px; font-weight: 800;
          margin-left: auto;
        }
        .uc13-old .uc13-side-foot .p { color: #ef4444; text-decoration: line-through; text-decoration-thickness: 1.5px; }
        .uc13-neu .uc13-side-foot .p { color: var(--it-color); }
        .uc13-arrow {
          font-size: 26px; color: var(--it-color); font-weight: 900;
          display: flex; align-items: center; justify-content: center;
        }

        @media (max-width: 1080px) {
          .uc13-kpis { grid-template-columns: 1fr 1fr; }
          .uc13-device-zoom .mc-page { zoom: 2.6; }
          .uc13-device-stage { min-height: 360px; padding: 20px; }
        }
        @media (max-width: 880px) {
          .uc13-stage { grid-template-columns: 1fr; }
          .uc13-list { position: static; flex-direction: row; overflow-x: auto; padding-bottom: 4px; }
          .uc13-item { min-width: 200px; }
          .uc13-show-inner { padding: 20px; gap: 16px; }
          .uc13-show-top { grid-template-columns: 1fr; }
          .uc13-show-tags { max-width: 100%; justify-content: flex-start; }
          .uc13-device-stage { min-height: 300px; padding: 14px; }
          .uc13-device-zoom { max-width: 100%; padding: 10px; }
          .uc13-device-zoom .mc-page { zoom: 2.0; }
          .uc13-tabs { display: none; }
          .uc13-vs { grid-template-columns: 1fr; }
          .uc13-arrow { transform: rotate(90deg); }
        }
      `}),(0,r.jsxs)("div",{className:"uc13-inner",children:[(0,r.jsxs)("div",{className:"uc13-head",children:[(0,r.jsx)("div",{className:"uc13-eyebrow",children:"8 \u0433\u043E\u0442\u043E\u0432\u044B\u0445 \u0441\u0446\u0435\u043D\u0430\u0440\u0438\u0435\u0432"}),(0,r.jsx)("h2",{className:"uc13-title",children:"\u041E\u0434\u0438\u043D \u043F\u0440\u043E\u043C\u043F\u0442 \u2014 \u0440\u0430\u0431\u043E\u0447\u0438\u0439 \u043F\u0440\u043E\u0434\u0443\u043A\u0442"}),(0,r.jsx)("p",{className:"uc13-sub",children:"\u041A\u043B\u0438\u043A\u043D\u0438\u0442\u0435 \u043F\u043E \u0441\u0446\u0435\u043D\u0430\u0440\u0438\u044E \u2014 \u043F\u043E\u0441\u043C\u043E\u0442\u0440\u0438\u0442\u0435 \u043A\u0430\u043A \u0432\u044B\u0433\u043B\u044F\u0434\u0438\u0442 \u0440\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442"})]}),(0,r.jsxs)("div",{className:"uc13-stage",children:[(0,r.jsx)("div",{className:"uc13-list",children:ds.map((o,s)=>{let l={"--it-accent":o.accent+"22","--it-border":o.accent+"80","--it-color":o.accent};return(0,r.jsxs)("div",{className:`uc13-item ${s===e?"on":""}`,style:l,onClick:()=>{t(s),a(!1)},children:[(0,r.jsx)("span",{className:"ic",children:o.icon}),(0,r.jsx)("span",{className:"nm",children:o.n}),(0,r.jsx)("span",{className:"arr",children:"\u2192"})]},s)})}),(0,r.jsx)("div",{className:"uc13-show",style:{"--it-color":i.accent,"--it-bg":`radial-gradient(circle at 70% 20%, ${i.accent}30, transparent 50%)`,"--it-grad":cd[i.kind]},children:(0,r.jsxs)("div",{className:"uc13-show-inner",children:[(0,r.jsxs)("div",{className:"uc13-show-top",children:[(0,r.jsxs)("div",{className:"uc13-show-top-left",children:[(0,r.jsx)("div",{className:"ic",children:i.icon}),(0,r.jsxs)("div",{children:[(0,r.jsxs)("div",{className:"uc13-show-cat",children:[i.n," ",(0,r.jsx)("span",{className:"uc13-tier-pill",children:i.tier})]}),(0,r.jsx)("div",{className:"uc13-show-name",children:i.prompt})]})]}),(0,r.jsx)("div",{className:"uc13-show-tags",children:i.tags.map((o,s)=>(0,r.jsx)("span",{className:"uc13-tag",children:o},s))})]}),(0,r.jsx)(jm,{it:i}),(0,r.jsxs)("div",{className:"uc13-kpis",children:[i.kpis.map((o,s)=>(0,r.jsxs)("div",{className:"uc13-kpi",children:[(0,r.jsx)("span",{className:"kv",children:o.k}),(0,r.jsx)("span",{className:"kl",children:o.v})]},s)),(0,r.jsxs)("div",{className:"uc13-kpi",children:[(0,r.jsx)("span",{className:"kv",children:i.neuTime}),(0,r.jsx)("span",{className:"kl",children:"\u043E\u0442 \u0447\u0430\u0442\u0430 \u0434\u043E \u0434\u043E\u043C\u0435\u043D\u0430"})]}),(0,r.jsxs)("div",{className:"uc13-kpi accent",children:[(0,r.jsx)("span",{className:"kv",children:i.saved}),(0,r.jsx)("span",{className:"kl",children:"\u0431\u044B\u0441\u0442\u0440\u0435\u0435 \u043E\u0431\u044B\u0447\u043D\u043E\u0433\u043E"})]})]}),(0,r.jsxs)("div",{className:"uc13-vs",children:[(0,r.jsxs)("div",{className:"uc13-side uc13-old",children:[(0,r.jsxs)("div",{className:"uc13-side-head",children:[(0,r.jsx)("span",{className:"uc13-side-lbl",children:"\u041A\u0430\u043A \u043E\u0431\u044B\u0447\u043D\u043E"}),(0,r.jsx)("span",{className:"uc13-side-cmp",children:i.competitor})]}),(0,r.jsx)("ul",{className:"uc13-side-list",children:i.pains.map((o,s)=>(0,r.jsxs)("li",{children:[(0,r.jsx)("span",{className:"mark",children:"\u2715"}),o]},s))}),(0,r.jsxs)("div",{className:"uc13-side-foot",children:[(0,r.jsxs)("span",{className:"t",children:["\u23F1 ",i.oldTime]}),(0,r.jsx)("span",{className:"p",children:i.oldPrice})]})]}),(0,r.jsx)("div",{className:"uc13-arrow",children:"\u2192"}),(0,r.jsxs)("div",{className:"uc13-side uc13-neu",children:[(0,r.jsxs)("div",{className:"uc13-side-head",children:[(0,r.jsx)("span",{className:"uc13-side-lbl",children:"\u0412 Omnia"}),(0,r.jsx)("span",{className:"uc13-side-cmp",children:"\u0427\u0430\u0442 \u0441 \u0418\u0418 \xB7 \u0434\u043E\u043C\u0435\u043D \xB7 152-\u0424\u0417"})]}),(0,r.jsx)("ul",{className:"uc13-side-list",children:i.gains.map((o,s)=>(0,r.jsxs)("li",{children:[(0,r.jsx)("span",{className:"mark",children:"\u2713"}),o]},s))}),(0,r.jsxs)("div",{className:"uc13-side-foot",children:[(0,r.jsxs)("span",{className:"t",children:["\u23F1 ",i.neuTime]}),(0,r.jsx)("span",{className:"p",children:i.neuPrice})]})]})]})]})},e)]})]})]})}function jm({it:e}){let[t,n]=I(1);return W(()=>{n(1)},[e.kind]),(0,r.jsxs)("div",{className:"uc13-device",children:[(0,r.jsxs)("div",{className:"uc13-device-chrome",children:[(0,r.jsxs)("div",{className:"uc13-device-dots",children:[(0,r.jsx)("span",{}),(0,r.jsx)("span",{}),(0,r.jsx)("span",{})]}),(0,r.jsxs)("div",{className:"uc13-device-url",children:[(0,r.jsx)("span",{className:"lock",children:"\u{1F512}"}),(0,r.jsx)("span",{className:"u-scheme",children:"https://"}),(0,r.jsx)("span",{className:"u-host",children:e.domain})]}),(0,r.jsxs)("div",{className:"uc13-device-actions",children:[(0,r.jsx)("div",{className:"uc13-tabs",children:[1,2,3].map(a=>(0,r.jsxs)("button",{className:`uc13-tabv ${a===t?"on":""}`,onClick:()=>n(a),children:["v",a]},a))}),(0,r.jsx)("span",{className:"badge",children:"live"})]})]}),(0,r.jsx)("div",{className:"uc13-device-stage",children:(0,r.jsx)("div",{className:"uc13-device-zoom",children:(0,r.jsx)(pd,{kind:e.kind,variant:t})},`${e.kind}-${t}`)})]})}var Dm=[{viz:"models",t:"10 \u0418\u0418-\u043C\u043E\u0434\u0435\u043B\u0435\u0439 \u043D\u0430 \u0432\u044B\u0431\u043E\u0440",d:"\u0420\u043E\u0441\u0441\u0438\u0439\u0441\u043A\u0438\u0439 \u0441\u0442\u0435\u043A \u043F\u043E \u0443\u043C\u043E\u043B\u0447\u0430\u043D\u0438\u044E: YandexGPT, GigaChat, DeepSeek. \u041F\u043B\u044E\u0441 Claude Sonnet 4.5, Opus, Haiku, Gemini, GPT-4. \u0411\u0435\u0437 VPN, \u0431\u0435\u0437 MIR-\u043A\u0430\u0440\u0442.",tag:"ai-stack",a:"#7c5cff",stats:[{k:"10",v:"\u043C\u043E\u0434\u0435\u043B\u0435\u0439"},{k:"4 \u0420\u0424",v:"\u043F\u0440\u043E\u0432\u0430\u0439\u0434\u0435\u0440\u0430"}]},{viz:"patterns",t:"7 \u043E\u0442\u0440\u0430\u0441\u043B\u0435\u0432\u044B\u0445 \u043F\u0430\u0442\u0442\u0435\u0440\u043D\u043E\u0432",d:"\u0418\u0418 \u0430\u0432\u0442\u043E\u043C\u0430\u0442\u0438\u0447\u0435\u0441\u043A\u0438 \u043F\u043E\u0434\u0431\u0438\u0440\u0430\u0435\u0442 \u0448\u0430\u0431\u043B\u043E\u043D \u043F\u043E\u0434 \u043E\u043F\u0438\u0441\u0430\u043D\u0438\u0435: \u0431\u0430\u0440\u0431\u0435\u0440\u0448\u043E\u043F / \u043C\u0430\u0433\u0430\u0437\u0438\u043D / \u043C\u0435\u0441\u0441\u0435\u043D\u0434\u0436\u0435\u0440 / RAG-\u0431\u043E\u0442 / \u043C\u0430\u0440\u043A\u0435\u0442\u043F\u043B\u0435\u0439\u0441 / CRM / SaaS. \u041A\u043B\u0438\u0435\u043D\u0442 \u043D\u0435 \u0432\u044B\u0431\u0438\u0440\u0430\u0435\u0442 \u2014 \u0432\u0438\u0434\u0438\u0442 \u0447\u0430\u0442.",tag:"patterns",a:"#0ea5e9",stats:[{k:"7",v:"\u043E\u0442\u0440\u0430\u0441\u043B\u0435\u0439"},{k:"8+",v:"\u043A\u0430\u0442\u0435\u0433\u043E\u0440\u0438\u0439"}]},{viz:"deploy",t:"\u0414\u0435\u043F\u043B\u043E\u0439 \u0432 30\u2013120 \u0441\u0435\u043A\u0443\u043D\u0434",d:"\u0410\u0432\u0442\u043E-\u0434\u0435\u043F\u043B\u043E\u0439 \u043D\u0430 \u043D\u0430\u0448 \u0441\u0435\u0440\u0432\u0435\u0440, SSL, CDN, \u043C\u0438\u0433\u0440\u0430\u0446\u0438\u0438 \u0411\u0414, push-\u0441\u0435\u0440\u0442\u0438\u0444\u0438\u043A\u0430\u0442\u044B PWA. \u041E\u0434\u043D\u0430 \u043A\u043D\u043E\u043F\u043A\u0430 \xAB\u041E\u043F\u0443\u0431\u043B\u0438\u043A\u043E\u0432\u0430\u0442\u044C\xBB \u0438\u0437 \u0447\u0430\u0442\u0430 \u2192 \u043F\u0440\u043E\u0434\u0430\u043A\u0448\u0435\u043D.",tag:"deploy",a:"#10b981",stats:[{k:"~60\u0441",v:"\u043C\u0435\u0434\u0438\u0430\u043D\u0430"},{k:"99.9%",v:"uptime"}]},{viz:"integrations",t:"CRM \u0438 Telegram-\u0431\u043E\u0442\u044B \u0432 \u043F\u043E\u0434\u043F\u0438\u0441\u043A\u0435",d:"\u0411\u0438\u0442\u0440\u0438\u043A\u044124, AmoCRM, Telegram, \u0412\u041A\u043E\u043D\u0442\u0430\u043A\u0442\u0435 \u2014 \u0433\u043E\u0442\u043E\u0432\u044B\u0435 \u0438\u043D\u0442\u0435\u0433\u0440\u0430\u0446\u0438\u0438 \u043D\u0430 \u0442\u0430\u0440\u0438\u0444\u0430\u0445 Pro \u0438 \u0432\u044B\u0448\u0435. \u0411\u0435\u0437 \u043E\u0442\u0434\u0435\u043B\u044C\u043D\u044B\u0445 \u043F\u043E\u0434\u043F\u0438\u0441\u043E\u043A Salebot.",tag:"integrations",a:"#ec4cb8",stats:[{k:"4+",v:"CRM"},{k:"0 \u20BD",v:"\u0434\u043E\u043F\u043B\u0430\u0442"}]},{viz:"versions",t:"\u0412\u0435\u0440\u0441\u0438\u0438 \u043A\u0430\u043A \u0432 \u043A\u0438\u043D\u043E",d:"\u0421\u043D\u0430\u043F\u0448\u043E\u0442 \u043F\u043E\u0441\u043B\u0435 \u043A\u0430\u0436\u0434\u043E\u0433\u043E \u043F\u0440\u043E\u043C\u043F\u0442\u0430. \u041E\u0442\u043A\u0430\u0442 \u0432 1 \u0441\u0435\u043A\u0443\u043D\u0434\u0443 \u2014 \u0431\u0435\u0437 git, \u0431\u0435\u0437 \u0442\u0435\u0440\u043C\u0438\u043D\u0430\u043B\u0430. \u0412\u0441\u0435 \u043F\u0440\u0430\u0432\u043A\u0438 \u0442\u0435\u043A\u0441\u0442\u043E\u043C: \xAB\u0441\u0434\u0435\u043B\u0430\u0439 \u0448\u0430\u043F\u043A\u0443 \u0442\u0435\u043C\u043D\u0435\u0435\xBB, \xAB\u0434\u043E\u0431\u0430\u0432\u044C \u043A\u043E\u043C\u043D\u0430\u0442\u044B\xBB.",tag:"versions",a:"#f59e0b",stats:[{k:"1\u0441",v:"\u043E\u0442\u043A\u0430\u0442"},{k:"\u221E",v:"\u0432\u0435\u0440\u0441\u0438\u0439"}]},{viz:"domain",t:"\u0421\u0432\u043E\u0439 \u0434\u043E\u043C\u0435\u043D \u043F\u0440\u044F\u043C\u043E \u0432 \u043F\u043B\u0430\u0442\u0444\u043E\u0440\u043C\u0435",d:"\u041F\u0440\u0438\u0434\u0443\u043C\u0430\u0439\u0442\u0435 \u0438\u043C\u044F \u2192 \u0418\u0418 \u043F\u043E\u043A\u0430\u0436\u0435\u0442 \u0441\u0432\u043E\u0431\u043E\u0434\u043D\u044B\u0435 .ru/.\u0440\u0444/.com/.pro \u2192 \u043A\u0443\u043F\u0438\u0442\u0435 \u043A\u0430\u0440\u0442\u043E\u0439 \u041C\u0418\u0420 \u2192 \u0430\u0432\u0442\u043E-\u043F\u0440\u0438\u0432\u044F\u0437\u043A\u0430. DNS / FTP / \u0431\u0438\u043B\u0434 \u043D\u0435 \u043D\u0443\u0436\u043D\u044B.",tag:"domain",a:"#06b6d4",stats:[{k:".ru",v:"\u0438\u0437 \u043A\u043E\u0440\u043E\u0431\u043A\u0438"},{k:"\u041C\u0418\u0420",v:"\u043E\u043F\u043B\u0430\u0442\u0430"}]}];function Om({kind:e,a:t}){return e==="models"?(0,r.jsx)(Am,{a:t}):e==="patterns"?(0,r.jsx)(Fm,{a:t}):e==="deploy"?(0,r.jsx)(Bm,{a:t}):e==="integrations"?(0,r.jsx)(Um,{a:t}):e==="versions"?(0,r.jsx)(Vm,{a:t}):e==="domain"?(0,r.jsx)(Wm,{a:t}):null}function Am({a:e}){return(0,r.jsxs)("div",{className:"fv-models",children:[(0,r.jsx)("style",{children:`
        .fv-models { display: grid; grid-template-columns: 1fr 1fr; gap: 9px; padding: 8px; width: 100%; }
        .fv-mod {
          padding: 11px 13px; border-radius: 10px;
          background: rgba(0,0,0,0.35); backdrop-filter: blur(8px);
          border: 1px solid rgba(255,255,255,0.08);
          display: flex; align-items: center; gap: 9px;
          transition: transform .25s, border-color .25s;
        }
        .fv-mod:hover { transform: translateY(-2px); border-color: var(--mc); }
        .fv-mod .dot {
          width: 10px; height: 10px; border-radius: 50%; background: var(--mc);
          box-shadow: 0 0 10px var(--mc); flex-shrink: 0;
        }
        .fv-mod .nm { font-size: 13px; font-weight: 700; color: #fff; flex: 1; }
        .fv-mod .tg {
          padding: 2px 6px; border-radius: 4px;
          font-family: 'JetBrains Mono', monospace; font-size: 9.5px; font-weight: 800;
          background: rgba(255,255,255,0.08); color: rgba(255,255,255,0.7);
        }
        .fv-mod .tg.ru { background: rgba(76,217,164,0.18); color: #4cd9a4; }
        .fv-mod .tg.st { background: rgba(255,210,90,0.18); color: #ffd25a; }
      `}),[{n:"YandexGPT",c:"#ffcc00",tag:"\u0420\u0424"},{n:"GigaChat",c:"#5cd2c8",tag:"\u0420\u0424"},{n:"DeepSeek",c:"#7c5cff",tag:"\u0420\u0424"},{n:"Sonnet 4.5",c:"#cc785c",tag:"\u2605"},{n:"Opus",c:"#a0653a"},{n:"Haiku",c:"#e8a07a"},{n:"Gemini 2.5",c:"#4285f4"},{n:"GPT-4.1",c:"#10a37f"}].map((n,a)=>(0,r.jsxs)("div",{className:"fv-mod",style:{"--mc":n.c},children:[(0,r.jsx)("span",{className:"dot"}),(0,r.jsx)("span",{className:"nm",children:n.n}),n.tag==="\u0420\u0424"&&(0,r.jsx)("span",{className:"tg ru",children:n.tag}),n.tag==="\u2605"&&(0,r.jsx)("span",{className:"tg st",children:n.tag})]},a))]})}function Fm({a:e}){return(0,r.jsxs)("div",{className:"fv-pat",children:[(0,r.jsx)("style",{children:`
        .fv-pat { display: flex; flex-wrap: wrap; gap: 7px; padding: 12px; align-content: center; justify-content: center; }
        .fv-pat .p {
          padding: 9px 14px; border-radius: 999px;
          background: rgba(0,0,0,0.35); backdrop-filter: blur(8px);
          border: 1px solid rgba(255,255,255,0.08);
          display: inline-flex; align-items: center; gap: 7px;
          transition: transform .25s, background .25s, border-color .25s;
          cursor: default;
        }
        .fv-pat .p:hover {
          transform: translateY(-2px); border-color: var(--ph);
          background: rgba(0,0,0,0.5);
        }
        .fv-pat .p .ic { font-size: 15px; }
        .fv-pat .p .n { font-size: 13px; font-weight: 700; color: #fff; }
        .fv-pat .more {
          padding: 9px 14px; border-radius: 999px;
          font-family: 'JetBrains Mono', monospace; font-size: 12px;
          color: rgba(255,255,255,0.6); border: 1px dashed rgba(255,255,255,0.15);
        }
      `}),[{ic:"\u{1F3EA}",n:"\u0421\u0430\u0439\u0442-\u0432\u0438\u0437\u0438\u0442\u043A\u0430",h:"#7c5cff"},{ic:"\u{1F6CD}",n:"\u041C\u0430\u0433\u0430\u0437\u0438\u043D",h:"#0ea5e9"},{ic:"\u{1F4AC}",n:"\u041C\u0435\u0441\u0441\u0435\u043D\u0434\u0436\u0435\u0440",h:"#10b981"},{ic:"\u{1F9E0}",n:"\u0418\u0418-\u0430\u0433\u0435\u043D\u0442",h:"#ec4cb8"},{ic:"\u26A1",n:"SaaS",h:"#f97316"},{ic:"\u{1F6D2}",n:"\u041C\u0430\u0440\u043A\u0435\u0442\u043F\u043B\u0435\u0439\u0441",h:"#06b6d4"},{ic:"\u{1F4CA}",n:"CRM",h:"#fbbf24"}].map((n,a)=>(0,r.jsxs)("div",{className:"p",style:{"--ph":n.h},children:[(0,r.jsx)("span",{className:"ic",children:n.ic}),(0,r.jsx)("span",{className:"n",children:n.n})]},a)),(0,r.jsx)("div",{className:"more",children:"+ \u0435\u0449\u0451 1"})]})}function Bm({a:e}){let[t,n]=I(0);W(()=>{let i,o=()=>{n(0);let s=0,l=()=>{s+=4+Math.random()*6,s>=100?(s=100,n(100),i=setTimeout(o,2200)):(n(s),i=setTimeout(l,100))};i=setTimeout(l,300)};return o(),()=>clearTimeout(i)},[]);let a=[{l:"\u0421\u0431\u043E\u0440\u043A\u0430",end:25},{l:"\u0424\u0430\u0439\u043B\u044B",end:50},{l:"\u0411\u0430\u0437\u0430 + SSL",end:80},{l:"\u041E\u043F\u0443\u0431\u043B\u0438\u043A\u043E\u0432\u0430\u043D\u043E",end:100}];return(0,r.jsxs)("div",{className:"fv-dep",children:[(0,r.jsx)("style",{children:`
        .fv-dep { padding: 18px; display: flex; flex-direction: column; gap: 14px; width: 100%; }
        .fv-dep-server {
          padding: 13px 15px; border-radius: 10px;
          background: rgba(0,0,0,0.4); border: 1px solid rgba(255,255,255,0.06);
          display: flex; align-items: center; gap: 12px;
        }
        .fv-dep-server .ic {
          width: 36px; height: 36px; border-radius: 9px;
          background: linear-gradient(135deg, ${e}, color-mix(in srgb, ${e}, white 25%));
          display: inline-flex; align-items: center; justify-content: center;
          font-size: 17px; box-shadow: 0 8px 20px -4px ${e};
        }
        .fv-dep-server .nm { font-family: 'JetBrains Mono', monospace; font-size: 13px; color: #fff; font-weight: 700; }
        .fv-dep-server .meta { font-size: 11px; color: rgba(255,255,255,0.6); margin-top: 2px; }
        .fv-dep-server .live {
          margin-left: auto; padding: 4px 9px 4px 7px; border-radius: 999px;
          background: rgba(76,217,164,0.15); color: #4cd9a4;
          font-family: 'JetBrains Mono', monospace; font-size: 11px; font-weight: 700;
          display: inline-flex; align-items: center; gap: 5px;
        }
        .fv-dep-server .live::before { content:''; width: 6px; height: 6px; border-radius: 50%; background: #4cd9a4; box-shadow: 0 0 6px #4cd9a4; animation: pulse-dot 1.4s infinite; }
        .fv-dep-prog {
          padding: 13px 15px; border-radius: 10px;
          background: rgba(0,0,0,0.4); border: 1px solid rgba(255,255,255,0.06);
        }
        .fv-dep-prog-head {
          display: flex; align-items: center; justify-content: space-between;
          margin-bottom: 9px;
          font-family: 'JetBrains Mono', monospace; font-size: 12px; color: #fff;
        }
        .fv-dep-prog-head .pct { color: ${e}; font-weight: 800; }
        .fv-dep-bar { height: 6px; border-radius: 999px; background: rgba(255,255,255,0.06); overflow: hidden; }
        .fv-dep-bar .fill {
          height: 100%; background: linear-gradient(90deg, ${e}, color-mix(in srgb, ${e}, white 30%));
          transition: width .15s linear;
        }
        .fv-dep-steps {
          display: flex; gap: 6px; flex-wrap: wrap; margin-top: 10px;
        }
        .fv-dep-st {
          padding: 4px 9px; border-radius: 6px;
          font-family: 'JetBrains Mono', monospace; font-size: 10.5px; font-weight: 700;
          background: rgba(255,255,255,0.04); color: rgba(255,255,255,0.5);
          border: 1px solid rgba(255,255,255,0.06);
          display: inline-flex; align-items: center; gap: 4px;
          transition: all .25s;
        }
        .fv-dep-st.done {
          background: rgba(76,217,164,0.15); color: #4cd9a4; border-color: rgba(76,217,164,0.3);
        }
        .fv-dep-st.now {
          background: ${e}25; color: ${e}; border-color: ${e};
        }
      `}),(0,r.jsxs)("div",{className:"fv-dep-server",children:[(0,r.jsx)("div",{className:"ic",children:"\u{1F5A5}"}),(0,r.jsxs)("div",{children:[(0,r.jsx)("div",{className:"nm",children:"ru-1.omnia.app"}),(0,r.jsx)("div",{className:"meta",children:"4 vCPU \xB7 8 GB \xB7 \u041C\u043E\u0441\u043A\u0432\u0430 \xB7 152-\u0424\u0417"})]}),(0,r.jsx)("div",{className:"live",children:"live"})]}),(0,r.jsxs)("div",{className:"fv-dep-prog",children:[(0,r.jsxs)("div",{className:"fv-dep-prog-head",children:[(0,r.jsx)("span",{children:t<100?"\u0420\u0430\u0437\u0432\u0451\u0440\u0442\u044B\u0432\u0430\u043D\u0438\u0435":"\u2713 \u0413\u043E\u0442\u043E\u0432\u043E \xB7 47 \u0441\u0435\u043A\u0443\u043D\u0434"}),(0,r.jsxs)("span",{className:"pct",children:[Math.floor(t),"%"]})]}),(0,r.jsx)("div",{className:"fv-dep-bar",children:(0,r.jsx)("div",{className:"fill",style:{width:`${t}%`}})}),(0,r.jsx)("div",{className:"fv-dep-steps",children:a.map((i,o)=>(0,r.jsxs)("div",{className:`fv-dep-st ${t>=i.end?"done":t>(a[o-1]?.end??0)?"now":""}`,children:[t>=i.end?"\u2713":t>(a[o-1]?.end??0)?"\u25CF":"\u25CB"," ",i.l]},o))})]})]})}function Um({a:e}){return(0,r.jsxs)("div",{className:"fv-int",children:[(0,r.jsx)("style",{children:`
        .fv-int { padding: 18px; display: grid; grid-template-columns: 1fr 1fr; gap: 9px; }
        .fv-app {
          padding: 11px 13px; border-radius: 10px;
          background: rgba(0,0,0,0.4); border: 1px solid rgba(255,255,255,0.06);
          display: flex; align-items: center; gap: 11px;
          transition: transform .25s, border-color .25s;
        }
        .fv-app:hover { transform: translateY(-2px); border-color: var(--ac); }
        .fv-app .ic-wrap {
          width: 28px; height: 28px; border-radius: 8px;
          background: var(--ac);
          display: inline-flex; align-items: center; justify-content: center;
          font-size: 12px; font-weight: 800; color: #fff;
          box-shadow: 0 4px 12px -2px var(--ac);
          flex-shrink: 0;
        }
        .fv-app .nm { font-size: 13px; font-weight: 700; color: #fff; flex: 1; }
        .fv-app .ok { color: #4cd9a4; font-size: 14px; font-weight: 800; }
      `}),[{n:"Telegram",c:"#229ED9",ic:"\u2708"},{n:"\u0412\u041A\u043E\u043D\u0442\u0430\u043A\u0442\u0435",c:"#0077FF",ic:"VK"},{n:"\u0411\u0438\u0442\u0440\u0438\u043A\u044124",c:"#2FC6F6",ic:"\u041124"},{n:"AmoCRM",c:"#FF7600",ic:"a"},{n:"WhatsApp",c:"#25D366",ic:"W"},{n:"\u042EKassa",c:"#FA7900",ic:"\u20BD"}].map((n,a)=>(0,r.jsxs)("div",{className:"fv-app",style:{"--ac":n.c},children:[(0,r.jsx)("span",{className:"ic-wrap",children:n.ic}),(0,r.jsx)("span",{className:"nm",children:n.n}),(0,r.jsx)("span",{className:"ok",children:"\u2713"})]},a))]})}function Vm({a:e}){let t=[{v:"v1.3",d:"\u041F\u043E\u043C\u0435\u043D\u044F\u043B \u043F\u0430\u043B\u0438\u0442\u0440\u0443 + \u043D\u043E\u0432\u044B\u0439 hero",t:"\u0441\u0435\u0439\u0447\u0430\u0441",active:!0},{v:"v1.2",d:"+ \u0444\u043E\u0440\u043C\u0430 \u0431\u0440\u043E\u043D\u0438, \u043C\u043E\u0431\u0438\u043B\u044C\u043D\u0430\u044F \u0432\u0451\u0440\u0441\u0442\u043A\u0430",t:"14 \u043C\u0438\u043D"},{v:"v1.1",d:"+ \u0441\u0435\u043A\u0446\u0438\u044F \u043C\u0435\u043D\u044E (6 \u043F\u043E\u0437\u0438\u0446\u0438\u0439)",t:"32 \u043C\u0438\u043D"},{v:"v1.0",d:"\u041F\u0435\u0440\u0432\u0430\u044F \u0432\u0435\u0440\u0441\u0438\u044F",t:"1 \u0447\u0430\u0441"}];return(0,r.jsxs)("div",{className:"fv-ver",children:[(0,r.jsx)("style",{children:`
        .fv-ver { padding: 18px; display: flex; flex-direction: column; gap: 8px; position: relative; }
        .fv-ver::before {
          content:''; position: absolute; left: 28px; top: 30px; bottom: 30px; width: 2px;
          background: linear-gradient(180deg, ${e}, ${e}30);
        }
        .fv-v {
          padding: 10px 14px 10px 38px; border-radius: 10px;
          background: rgba(0,0,0,0.4); border: 1px solid rgba(255,255,255,0.06);
          position: relative;
          transition: all .25s;
        }
        .fv-v:hover { border-color: ${e}50; }
        .fv-v::before {
          content:''; position: absolute; left: 14px; top: 50%; transform: translateY(-50%);
          width: 12px; height: 12px; border-radius: 50%;
          background: rgba(0,0,0,0.4); border: 2px solid rgba(255,255,255,0.2);
        }
        .fv-v.on::before {
          background: ${e}; border-color: ${e};
          box-shadow: 0 0 0 4px ${e}25;
        }
        .fv-v-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 3px; }
        .fv-v-name { font-family: 'JetBrains Mono', monospace; font-size: 13px; font-weight: 800; color: #fff; }
        .fv-v.on .fv-v-name { color: ${e}; }
        .fv-v-time { font-family: 'JetBrains Mono', monospace; font-size: 10.5px; color: rgba(255,255,255,0.5); }
        .fv-v-desc { font-size: 12px; color: rgba(255,255,255,0.7); line-height: 1.3; }
        .fv-v-undo {
          padding: 3px 8px; border-radius: 5px;
          background: ${e}20; color: ${e};
          font-family: 'JetBrains Mono', monospace; font-size: 10.5px; font-weight: 700;
          opacity: 0; transition: opacity .25s;
        }
        .fv-v:hover:not(.on) .fv-v-undo { opacity: 1; }
      `}),t.map((n,a)=>(0,r.jsxs)("div",{className:`fv-v ${n.active?"on":""}`,children:[(0,r.jsxs)("div",{className:"fv-v-row",children:[(0,r.jsx)("span",{className:"fv-v-name",children:n.v}),n.active?(0,r.jsx)("span",{className:"fv-v-time",children:"\u0441\u0435\u0439\u0447\u0430\u0441"}):(0,r.jsx)("span",{className:"fv-v-undo",children:"\u21B6 \u043E\u0442\u043A\u0430\u0442\u0438\u0442\u044C \xB7 1\u0441"})]}),(0,r.jsx)("div",{className:"fv-v-desc",children:n.d})]},a))]})}function Wm({a:e}){let t=[{d:"razor-barber.ru",avail:!0,price:"599 \u20BD/\u0433\u043E\u0434"},{d:"razor.\u0440\u0444",avail:!0,price:"199 \u20BD/\u0433\u043E\u0434"},{d:"razor-kazan.com",avail:!0,price:"1 290 \u20BD/\u0433\u043E\u0434"},{d:"razor.pro",avail:!1,price:"\u0437\u0430\u043D\u044F\u0442"}];return(0,r.jsxs)("div",{className:"fv-dom",children:[(0,r.jsx)("style",{children:`
        .fv-dom { padding: 18px; display: flex; flex-direction: column; gap: 10px; }
        .fv-dom-search {
          padding: 11px 14px; border-radius: 10px;
          background: rgba(0,0,0,0.5); border: 1px solid ${e}40;
          display: flex; align-items: center; gap: 10px;
          font-family: 'JetBrains Mono', monospace; font-size: 14px;
        }
        .fv-dom-search .ic { color: ${e}; }
        .fv-dom-search .q { color: #fff; font-weight: 700; }
        .fv-dom-search .cur {
          display: inline-block; width: 2px; height: 14px;
          background: ${e}; vertical-align: -2px;
          animation: typing-cursor 0.7s steps(1) infinite;
        }
        .fv-dom-list { display: flex; flex-direction: column; gap: 6px; }
        .fv-dom-opt {
          padding: 10px 14px; border-radius: 8px;
          background: rgba(0,0,0,0.35); border: 1px solid rgba(255,255,255,0.06);
          display: flex; align-items: center; gap: 10px;
          font-family: 'JetBrains Mono', monospace;
          transition: all .25s;
        }
        .fv-dom-opt.avail { cursor: pointer; }
        .fv-dom-opt.avail:hover { background: ${e}15; border-color: ${e}; }
        .fv-dom-opt .nm { font-size: 13.5px; color: #fff; font-weight: 600; flex: 1; }
        .fv-dom-opt .stat {
          padding: 2px 7px; border-radius: 4px;
          font-size: 10.5px; font-weight: 800;
        }
        .fv-dom-opt .stat.ok { background: rgba(76,217,164,0.18); color: #4cd9a4; }
        .fv-dom-opt .stat.no { background: rgba(239,68,68,0.18); color: #ef4444; }
        .fv-dom-opt .pr { font-size: 12px; color: rgba(255,255,255,0.7); min-width: 70px; text-align: right; }
        .fv-dom-mir {
          display: inline-flex; align-items: center; gap: 7px;
          padding: 5px 10px 5px 8px; border-radius: 999px;
          background: ${e}20; color: ${e};
          font-family: 'JetBrains Mono', monospace; font-size: 11px; font-weight: 800;
          align-self: flex-start;
        }
        .fv-dom-mir::before { content:'\u{1F1F7}\u{1F1FA}'; font-size: 10px; }
      `}),(0,r.jsxs)("div",{className:"fv-dom-search",children:[(0,r.jsx)("span",{className:"ic",children:"\u{1F50D}"}),(0,r.jsx)("span",{className:"q",children:"razor"}),(0,r.jsx)("span",{className:"cur"})]}),(0,r.jsx)("div",{className:"fv-dom-list",children:t.map((n,a)=>(0,r.jsxs)("div",{className:`fv-dom-opt ${n.avail?"avail":"taken"}`,children:[(0,r.jsx)("span",{className:"nm",children:n.d}),(0,r.jsx)("span",{className:`stat ${n.avail?"ok":"no"}`,children:n.avail?"\u0441\u0432\u043E\u0431\u043E\u0434\u0435\u043D":"\u0437\u0430\u043D\u044F\u0442"}),(0,r.jsx)("span",{className:"pr",children:n.price})]},a))}),(0,r.jsx)("div",{className:"fv-dom-mir",children:"\u043E\u043F\u043B\u0430\u0442\u0430 \u043A\u0430\u0440\u0442\u043E\u0439 \u041C\u0418\u0420 \xB7 \u0430\u0432\u0442\u043E-\u043F\u0440\u0438\u0432\u044F\u0437\u043A\u0430"})]})}function Hm(){return(0,r.jsxs)("section",{id:"features",className:"ft13",children:[(0,r.jsx)("style",{children:`
        .ft13 {
          padding: 100px 24px 60px;
          position: relative;
        }
        .ft13-inner { max-width: 1240px; margin: 0 auto; }
        .ft13-head { text-align: center; max-width: 760px; margin: 0 auto 64px; }
        .ft13-eyebrow {
          font-size: 12px; letter-spacing: 0.14em; text-transform: uppercase;
          color: var(--accent); font-weight: 700;
          display: inline-flex; align-items: center; gap: 10px; margin-bottom: 14px;
        }
        .ft13-eyebrow::before, .ft13-eyebrow::after {
          content:''; width: 18px; height: 1px; background: var(--accent);
        }
        .ft13-title {
          font-size: clamp(34px, 4.6vw, 60px);
          line-height: 1.0; letter-spacing: -0.04em;
          font-weight: 800; margin: 0;
          background: linear-gradient(135deg, #fff, #aaa);
          -webkit-background-clip: text; background-clip: text;
          -webkit-text-fill-color: transparent;
        }

        .ft13-stack {
          display: flex; flex-direction: column; gap: 18px;
        }
        .ft13-row {
          display: grid; grid-template-columns: 1fr 1fr; gap: 32px;
          align-items: center;
          padding: 36px;
          border-radius: 24px;
          background: rgba(20,20,27,0.5); backdrop-filter: blur(10px);
          border: 1px solid rgba(255,255,255,0.06);
          position: relative; overflow: hidden;
          opacity: 0; transform: translateY(40px);
          transition: opacity 0.8s, transform 0.8s cubic-bezier(.2,.8,.2,1);
        }
        .ft13-row.in { opacity: 1; transform: translateY(0); }
        .ft13-row::before {
          content:''; position: absolute; inset: 0;
          background: radial-gradient(circle at 80% 20%, var(--ft-glow), transparent 50%);
          opacity: 0.5;
          pointer-events: none;
        }
        .ft13-row.flip { grid-template-columns: 1fr 1fr; }
        .ft13-row.flip .ft13-text { order: 2; }
        .ft13-row.flip .ft13-visual { order: 1; }

        .ft13-text { position: relative; z-index: 1; }
        .ft13-tag {
          font-family: 'JetBrains Mono', monospace; font-size: 11px;
          color: var(--ft-color); font-weight: 700; letter-spacing: 0.08em;
          text-transform: uppercase; margin-bottom: 14px;
          display: inline-flex; align-items: center; gap: 7px;
        }
        .ft13-tag::before {
          content:''; width: 14px; height: 1px; background: var(--ft-color);
        }
        .ft13-row-title {
          font-size: clamp(26px, 3.4vw, 38px);
          line-height: 1.05; letter-spacing: -0.03em;
          font-weight: 800; color: #fff; margin: 0 0 12px;
        }
        .ft13-row-desc {
          font-size: 16px; color: var(--muted); line-height: 1.55;
          max-width: 38ch;
        }
        .ft13-stats {
          margin-top: 22px;
          display: flex; gap: 14px;
        }
        .ft13-stat {
          padding: 11px 16px; border-radius: 12px;
          background: rgba(8,8,12,0.5); border: 1px solid rgba(255,255,255,0.06);
          min-width: 90px;
        }
        .ft13-stat-v {
          display: block;
          font-size: 22px; font-weight: 900; letter-spacing: -0.02em;
          color: var(--ft-color); line-height: 1;
          font-variant-numeric: tabular-nums;
        }
        .ft13-stat-l {
          display: block;
          font-size: 11px; color: var(--muted); margin-top: 4px;
          font-family: 'JetBrains Mono', monospace;
        }

        /* Visual side \u2014 now hosts real product visualization */
        .ft13-visual {
          aspect-ratio: 4/3;
          background:
            radial-gradient(ellipse at 20% 20%, color-mix(in srgb, var(--ft-color), #000 50%), transparent 60%),
            radial-gradient(ellipse at 80% 80%, color-mix(in srgb, var(--ft-color), #000 65%), transparent 60%),
            #0d1018;
          border-radius: 18px;
          position: relative;
          overflow: hidden;
          display: flex; align-items: stretch; justify-content: stretch;
          box-shadow: 0 30px 60px -20px var(--ft-color), 0 0 0 1px rgba(255,255,255,0.05) inset;
        }
        .ft13-visual .grid-bg {
          position: absolute; inset: 0;
          background-image:
            linear-gradient(rgba(255,255,255,0.04) 1px, transparent 1px),
            linear-gradient(90deg, rgba(255,255,255,0.04) 1px, transparent 1px);
          background-size: 28px 28px;
          mask-image: radial-gradient(ellipse at center, black 30%, transparent 85%);
          pointer-events: none;
        }
        .ft13-visual .viz-wrap {
          position: relative; z-index: 2;
          width: 100%; height: 100%;
          display: flex; align-items: center; justify-content: center;
        }
        .ft13-visual .viz-wrap > div { width: 100%; height: 100%; box-sizing: border-box; }

        @media (max-width: 880px) {
          .ft13-row { grid-template-columns: 1fr; padding: 24px; gap: 22px; }
          .ft13-row.flip .ft13-text { order: 2; }
          .ft13-row.flip .ft13-visual { order: 1; }
          .ft13-visual { aspect-ratio: 16/10; }
        }
      `}),(0,r.jsxs)("div",{className:"ft13-inner",children:[(0,r.jsxs)("div",{className:"ft13-head",children:[(0,r.jsx)("div",{className:"ft13-eyebrow",children:"\u0412\u043E\u0437\u043C\u043E\u0436\u043D\u043E\u0441\u0442\u0438"}),(0,r.jsx)("h2",{className:"ft13-title",children:"\u041F\u043B\u0430\u0442\u0444\u043E\u0440\u043C\u0430, \u0430 \u043D\u0435 \u0433\u0435\u043D\u0435\u0440\u0430\u0442\u043E\u0440"})]}),(0,r.jsx)("div",{className:"ft13-stack",children:Dm.map((e,t)=>(0,r.jsx)(Ym,{f:e,flip:t%2===1},t))})]})]})}function Ym({f:e,flip:t}){let[n,a]=sd({threshold:.18});return(0,r.jsxs)("div",{ref:n,className:`ft13-row ${t?"flip":""} ${a?"in":""}`,style:{"--ft-color":e.a,"--ft-glow":e.a+"33"},children:[(0,r.jsxs)("div",{className:"ft13-text",children:[(0,r.jsx)("div",{className:"ft13-tag",children:e.tag}),(0,r.jsx)("h3",{className:"ft13-row-title",children:e.t}),(0,r.jsx)("p",{className:"ft13-row-desc",children:e.d}),(0,r.jsx)("div",{className:"ft13-stats",children:e.stats.map((i,o)=>(0,r.jsxs)("div",{className:"ft13-stat",children:[(0,r.jsx)("span",{className:"ft13-stat-v",children:i.k}),(0,r.jsx)("span",{className:"ft13-stat-l",children:i.v})]},o))})]}),(0,r.jsxs)("div",{className:"ft13-visual",children:[(0,r.jsx)("div",{className:"grid-bg"}),(0,r.jsx)("div",{className:"viz-wrap",children:(0,r.jsx)(Om,{kind:e.viz,a:e.a})})]})]})}var Fr=[{name:"Lite",audience:"\u0421\u0430\u043C\u043E\u0437\u0430\u043D\u044F\u0442\u044B\u0439 \xB7 1\u20135 \u0447\u0435\u043B.",price:990,proj:10,prompts:50,team:1,products:"\u0421\u0430\u0439\u0442-\u0432\u0438\u0437\u0438\u0442\u043A\u0430 \xB7 \u043B\u0435\u043D\u0434\u0438\u043D\u0433 \xB7 \u043C\u0438\u043D\u0438-\u0418\u041C \xB7 FAQ-\u0431\u043E\u0442",sup:"Email \xB7 \u0441\u043E\u043E\u0431\u0449\u0435\u0441\u0442\u0432\u043E",server:"\u041E\u0431\u0449\u0438\u0439",ssl:!0,crm:!1,exp:!1,runtime:!1,color:"#5cb8ff"},{name:"Solo",audience:"\u0424\u0440\u0438\u043B\u0430\u043D\u0441\u0435\u0440",price:2490,proj:20,prompts:90,team:1,products:"\u0418\u041C \u0441 \u043F\u043B\u0430\u0442\u0435\u0436\u0430\u043C\u0438 \xB7 PWA \xB7 \u0434\u0430\u0448\u0431\u043E\u0440\u0434 \u043A\u043B\u0438\u0435\u043D\u0442\u0443",sup:"Email \xB7 \u043F\u0440\u0438\u043E\u0440\u0438\u0442\u0435\u0442",server:"\u041E\u0431\u0449\u0438\u0439+",ssl:!0,crm:!1,exp:!1,runtime:!1,color:"#10b981"},{name:"Pro",audience:"\u041C\u0430\u043B\u043E\u0435 \u0430\u0433\u0435\u043D\u0442\u0441\u0442\u0432\u043E \xB7 3\u201310 \u0447\u0435\u043B.",price:8990,proj:1/0,prompts:120,team:10,products:"\u041C\u0435\u0441\u0441\u0435\u043D\u0434\u0436\u0435\u0440 \xB7 \u0418\u0418-\u0430\u0433\u0435\u043D\u0442 \xB7 \u043C\u0430\u0440\u043A\u0435\u0442\u043F\u043B\u0435\u0439\u0441-MVP \xB7 CRM-\u0438\u043D\u0442\u0435\u0433\u0440\u0430\u0446\u0438\u044F",sup:"\u0427\u0430\u0442 \u0441 \u0438\u043D\u0436\u0435\u043D\u0435\u0440\u043E\u043C \xB7 1 \u0447\u0430\u0441",server:"\u0412\u044B\u0434\u0435\u043B\u0435\u043D\u043D\u044B\u0439",ssl:!0,crm:!0,exp:!0,runtime:!1,color:"#7c5cff",featured:!0},{name:"Enterprise",audience:"\u041A\u043E\u043C\u043F\u0430\u043D\u0438\u044F \xB7 50\u2013500 \u0447\u0435\u043B.",price:24990,proj:1/0,prompts:818,team:1/0,products:"\u0418\u0418-\u0430\u0433\u0435\u043D\u0442\u044B \u0441 RAG \xB7 \u0433\u043E\u043B\u043E\u0441\u043E\u0432\u044B\u0435 \u0431\u043E\u0442\u044B \xB7 PWA \xB7 1\u0421 \xB7 152-\u0424\u0417",sup:"\u041F\u0435\u0440\u0441\u043E\u043D\u0430\u043B\u044C\u043D\u044B\u0439 \u0438\u043D\u0436\u0435\u043D\u0435\u0440 \xB7 SLA 99.9%",server:"\u0421\u0432\u043E\u0439 \u0440\u0435\u0433\u0438\u043E\u043D (RU-1/2/3)",ssl:!0,crm:!0,exp:!0,runtime:!0,color:"#f97316"}];function qm(){let[e,t]=I(8),[n,a]=I(60),[i,o]=I(3),s=id(()=>{for(let c=0;c<Fr.length;c++){let p=Fr[c];if(e<=p.proj&&n<=p.prompts&&i<=p.team)return c}return Fr.length-1},[e,n,i]),l=Fr[s];return(0,r.jsxs)("section",{id:"pricing",className:"pr13",children:[(0,r.jsx)("style",{children:`
        .pr13 { padding: 100px 24px; position: relative; overflow: hidden; }
        .pr13::before {
          content:''; position: absolute; top: -100px; left: 50%; transform: translateX(-50%);
          width: 900px; height: 600px;
          background: radial-gradient(ellipse, rgba(124,92,255,0.18), transparent 60%);
          filter: blur(60px);
        }
        .pr13-inner { max-width: 1240px; margin: 0 auto; position: relative; }
        .pr13-head { text-align: center; max-width: 760px; margin: 0 auto 56px; }
        .pr13-eyebrow {
          font-size: 12px; letter-spacing: 0.14em; text-transform: uppercase;
          color: var(--accent); font-weight: 700;
          display: inline-flex; align-items: center; gap: 10px; margin-bottom: 14px;
        }
        .pr13-eyebrow::before, .pr13-eyebrow::after {
          content:''; width: 18px; height: 1px; background: var(--accent);
        }
        .pr13-title {
          font-size: clamp(34px, 4.6vw, 60px);
          line-height: 1.0; letter-spacing: -0.04em;
          font-weight: 800; margin: 0 0 14px;
          background: linear-gradient(135deg, #fff, #aaa);
          -webkit-background-clip: text; background-clip: text;
          -webkit-text-fill-color: transparent;
        }
        .pr13-sub { font-size: 17px; color: var(--muted); }

        .pr13-stage {
          display: grid; grid-template-columns: 1fr 1fr; gap: 28px;
          align-items: stretch;
        }

        /* LEFT: calculator */
        .pr13-calc {
          background: rgba(20,20,27,0.7); backdrop-filter: blur(20px);
          border: 1px solid rgba(255,255,255,0.08);
          border-radius: 22px;
          padding: 32px;
          box-shadow: 0 40px 80px -30px rgba(0,0,0,0.6);
        }
        .pr13-calc-h {
          font-size: 19px; font-weight: 700; color: #fff;
          margin-bottom: 6px;
        }
        .pr13-calc-sub {
          font-size: 13.5px; color: var(--muted); margin-bottom: 28px;
        }
        .pr13-field { margin-bottom: 26px; }
        .pr13-field-label {
          display: flex; align-items: center; justify-content: space-between;
          margin-bottom: 12px;
        }
        .pr13-field-name {
          font-size: 14px; color: rgba(255,255,255,0.9); font-weight: 600;
        }
        .pr13-field-value {
          font-family: 'JetBrains Mono', monospace; font-size: 16px;
          color: var(--accent); font-weight: 800;
          font-variant-numeric: tabular-nums;
        }
        .pr13-slider {
          width: 100%; -webkit-appearance: none; appearance: none;
          height: 6px; border-radius: 999px;
          background: rgba(255,255,255,0.08);
          outline: none;
          cursor: pointer;
        }
        .pr13-slider::-webkit-slider-thumb {
          -webkit-appearance: none; appearance: none;
          width: 22px; height: 22px; border-radius: 50%;
          background: linear-gradient(135deg, #7c5cff, #c66dff);
          cursor: pointer;
          box-shadow: 0 4px 14px -4px rgba(124,92,255,0.6), 0 0 0 1px rgba(255,255,255,0.1);
          transition: transform .15s;
        }
        .pr13-slider::-webkit-slider-thumb:hover { transform: scale(1.15); }
        .pr13-slider::-moz-range-thumb {
          width: 22px; height: 22px; border-radius: 50%; border: 0;
          background: linear-gradient(135deg, #7c5cff, #c66dff);
          cursor: pointer;
          box-shadow: 0 4px 14px -4px rgba(124,92,255,0.6);
        }
        .pr13-marks {
          display: flex; justify-content: space-between;
          margin-top: 6px;
          font-family: 'JetBrains Mono', monospace; font-size: 10.5px; color: var(--muted-2);
        }
        .pr13-hint {
          margin-top: 6px;
          font-family: 'JetBrains Mono', monospace; font-size: 10.5px;
          color: var(--muted-2); font-style: italic;
        }

        /* RIGHT: tier recommendation + table */
        .pr13-result {
          background: linear-gradient(135deg, var(--pi-color)20, rgba(20,20,27,0.7));
          backdrop-filter: blur(20px);
          border: 1.5px solid var(--pi-color);
          border-radius: 22px;
          padding: 32px;
          display: flex; flex-direction: column; gap: 22px;
          box-shadow: 0 40px 80px -30px var(--pi-color);
          transition: all 0.4s cubic-bezier(.2,.8,.2,1);
        }
        .pr13-result-top { display: flex; align-items: center; gap: 14px; }
        .pr13-result-badge {
          padding: 5px 11px; border-radius: 999px;
          background: var(--pi-color); color: #fff;
          font-size: 11px; font-weight: 800; letter-spacing: 0.08em; text-transform: uppercase;
        }
        .pr13-result-title { font-size: 12px; color: var(--muted); letter-spacing: 0.08em; text-transform: uppercase; }
        .pr13-result-name { font-size: 32px; font-weight: 900; color: #fff; letter-spacing: -0.03em; line-height: 1; }
        .pr13-result-price {
          font-size: clamp(40px, 5vw, 60px); font-weight: 900;
          color: #fff; letter-spacing: -0.04em; line-height: 1;
          font-variant-numeric: tabular-nums;
          margin-top: 8px;
        }
        .pr13-result-price .small { font-size: 16px; font-weight: 600; color: var(--muted); margin-left: 6px; }
        .pr13-result-products {
          margin-top: 10px;
          padding: 8px 12px; border-radius: 8px;
          background: rgba(255,255,255,0.04);
          font-size: 12.5px; color: rgba(255,255,255,0.75); line-height: 1.4;
          font-style: italic;
        }
        .pr13-result-foot {
          margin-top: -4px;
          font-family: 'JetBrains Mono', monospace;
          font-size: 11px; color: var(--muted-2);
          text-align: center;
        }
        .pr13-result-features {
          display: flex; flex-direction: column; gap: 10px;
          padding-top: 14px;
          border-top: 1px solid rgba(255,255,255,0.08);
        }
        .pr13-feat {
          display: flex; align-items: center; gap: 12px;
          font-size: 14px; color: rgba(255,255,255,0.85);
        }
        .pr13-feat .check {
          width: 18px; height: 18px; border-radius: 50%;
          background: var(--pi-color);
          color: #fff;
          display: inline-flex; align-items: center; justify-content: center;
          font-size: 11px; font-weight: 800;
          flex-shrink: 0;
        }
        .pr13-feat .v {
          margin-left: auto; font-family: 'JetBrains Mono', monospace;
          font-size: 13px; color: #fff; font-weight: 700;
        }
        .pr13-result-cta {
          margin-top: 12px;
          padding: 14px 22px; border-radius: 999px;
          background: linear-gradient(135deg, var(--pi-color), color-mix(in srgb, var(--pi-color), white 20%));
          color: #fff; font-weight: 700; font-size: 15px;
          display: inline-flex; align-items: center; justify-content: center; gap: 8px;
          cursor: pointer;
          box-shadow: 0 16px 36px -10px var(--pi-color);
          transition: transform .2s;
        }
        .pr13-result-cta:hover { transform: translateY(-2px); }

        @media (max-width: 880px) {
          .pr13-stage { grid-template-columns: 1fr; }
          .pr13-calc, .pr13-result { padding: 24px; }
        }
      `}),(0,r.jsxs)("div",{className:"pr13-inner",children:[(0,r.jsxs)("div",{className:"pr13-head",children:[(0,r.jsx)("div",{className:"pr13-eyebrow",children:"\u0422\u0430\u0440\u0438\u0444\u044B"}),(0,r.jsx)("h2",{className:"pr13-title",children:"\u0421\u0430\u043C\u0438 \u0440\u0430\u0441\u0441\u0447\u0438\u0442\u0430\u0439\u0442\u0435 \u0441\u0432\u043E\u0439 \u043F\u043B\u0430\u043D"}),(0,r.jsx)("p",{className:"pr13-sub",children:"\u0414\u0432\u0438\u0433\u0430\u0439\u0442\u0435 \u0441\u043B\u0430\u0439\u0434\u0435\u0440\u044B \u2014 \u043C\u044B \u043F\u043E\u0434\u0431\u0435\u0440\u0451\u043C \u043E\u043F\u0442\u0438\u043C\u0430\u043B\u044C\u043D\u044B\u0439 \u0442\u0430\u0440\u0438\u0444"})]}),(0,r.jsxs)("div",{className:"pr13-stage",children:[(0,r.jsxs)("div",{className:"pr13-calc",children:[(0,r.jsx)("div",{className:"pr13-calc-h",children:"\u0427\u0442\u043E \u0432\u0430\u043C \u043D\u0443\u0436\u043D\u043E?"}),(0,r.jsx)("div",{className:"pr13-calc-sub",children:"3 \u043F\u0430\u0440\u0430\u043C\u0435\u0442\u0440\u0430 \u2014 \u0438 \u0432\u0438\u0434\u043D\u043E \u0437\u0430 \u0447\u0442\u043E \u043F\u043B\u0430\u0442\u0438\u0442\u0435"}),(0,r.jsxs)("div",{className:"pr13-field",children:[(0,r.jsxs)("div",{className:"pr13-field-label",children:[(0,r.jsx)("span",{className:"pr13-field-name",children:"\u0410\u043A\u0442\u0438\u0432\u043D\u044B\u0445 \u043F\u0440\u043E\u0435\u043A\u0442\u043E\u0432 \u0432 \u043F\u0440\u043E\u0434\u0435"}),(0,r.jsx)("span",{className:"pr13-field-value",children:e>=50?"50+":e})]}),(0,r.jsx)("input",{type:"range",min:"1",max:"50",value:e,className:"pr13-slider",onChange:c=>t(+c.target.value)}),(0,r.jsxs)("div",{className:"pr13-marks",children:[(0,r.jsx)("span",{children:"1"}),(0,r.jsx)("span",{children:"10"}),(0,r.jsx)("span",{children:"20"}),(0,r.jsx)("span",{children:"50+"})]})]}),(0,r.jsxs)("div",{className:"pr13-field",children:[(0,r.jsxs)("div",{className:"pr13-field-label",children:[(0,r.jsx)("span",{className:"pr13-field-name",children:"\u042D\u043A\u0432\u0438\u0432\u0430\u043B\u0435\u043D\u0442-\u0433\u0435\u043D\u0435\u0440\u0430\u0446\u0438\u0439 \u0432 \u043C\u0435\u0441\u044F\u0446"}),(0,r.jsx)("span",{className:"pr13-field-value",children:n>=1e3?"1k+":n.toLocaleString("ru-RU")})]}),(0,r.jsx)("input",{type:"range",min:"10",max:"1000",step:"10",value:n,className:"pr13-slider",onChange:c=>a(+c.target.value)}),(0,r.jsxs)("div",{className:"pr13-marks",children:[(0,r.jsx)("span",{children:"10"}),(0,r.jsx)("span",{children:"90"}),(0,r.jsx)("span",{children:"120"}),(0,r.jsx)("span",{children:"818+"})]}),(0,r.jsx)("div",{className:"pr13-hint",children:"\u0433\u0435\u043D\u0435\u0440\u0430\u0446\u0438\u044F = \u043F\u0440\u043E\u043C\u043F\u0442 + \u043F\u0440\u0430\u0432\u043A\u0438 + \u0438\u0442\u0435\u0440\u0430\u0446\u0438\u0438"})]}),(0,r.jsxs)("div",{className:"pr13-field",children:[(0,r.jsxs)("div",{className:"pr13-field-label",children:[(0,r.jsx)("span",{className:"pr13-field-name",children:"\u0420\u0430\u0437\u043C\u0435\u0440 \u043A\u043E\u043C\u0430\u043D\u0434\u044B"}),(0,r.jsx)("span",{className:"pr13-field-value",children:i>=100?"100+":i})]}),(0,r.jsx)("input",{type:"range",min:"1",max:"100",value:i,className:"pr13-slider",onChange:c=>o(+c.target.value)}),(0,r.jsxs)("div",{className:"pr13-marks",children:[(0,r.jsx)("span",{children:"1"}),(0,r.jsx)("span",{children:"10"}),(0,r.jsx)("span",{children:"50"}),(0,r.jsx)("span",{children:"500+"})]})]})]}),(0,r.jsxs)("div",{className:"pr13-result",style:{"--pi-color":l.color},children:[(0,r.jsxs)("div",{className:"pr13-result-top",children:[(0,r.jsx)("div",{className:"pr13-result-badge",children:l.featured?"\u2605 \u043F\u043E\u043F\u0443\u043B\u044F\u0440\u043D\u044B\u0439":"\u0440\u0435\u043A\u043E\u043C\u0435\u043D\u0434\u0443\u0435\u043C"}),(0,r.jsx)("div",{className:"pr13-result-title",children:l.audience})]}),(0,r.jsxs)("div",{children:[(0,r.jsx)("div",{className:"pr13-result-name",children:l.name}),(0,r.jsxs)("div",{className:"pr13-result-price",children:[l.price.toLocaleString("ru-RU")," \u20BD",(0,r.jsx)("span",{className:"small",children:"/ \u043C\u0435\u0441\u044F\u0446"})]}),(0,r.jsx)("div",{className:"pr13-result-products",children:l.products})]}),(0,r.jsxs)("div",{className:"pr13-result-features",children:[(0,r.jsxs)("div",{className:"pr13-feat",children:[(0,r.jsx)("span",{className:"check",children:"\u2713"}),(0,r.jsx)("span",{children:"\u0410\u043A\u0442\u0438\u0432\u043D\u044B\u0445 \u043F\u0440\u043E\u0435\u043A\u0442\u043E\u0432"}),(0,r.jsx)("span",{className:"v",children:l.proj===1/0?"\u221E":l.proj})]}),(0,r.jsxs)("div",{className:"pr13-feat",children:[(0,r.jsx)("span",{className:"check",children:"\u2713"}),(0,r.jsx)("span",{children:"\u0413\u0435\u043D\u0435\u0440\u0430\u0446\u0438\u0439 / \u043C\u0435\u0441"}),(0,r.jsxs)("span",{className:"v",children:["~",l.prompts===1/0?"\u221E":l.prompts]})]}),(0,r.jsxs)("div",{className:"pr13-feat",children:[(0,r.jsx)("span",{className:"check",children:"\u2713"}),(0,r.jsx)("span",{children:"\u041A\u043E\u043C\u0430\u043D\u0434\u0430 \u0434\u043E"}),(0,r.jsx)("span",{className:"v",children:l.team===1/0?"\u221E":l.team})]}),(0,r.jsxs)("div",{className:"pr13-feat",children:[(0,r.jsx)("span",{className:"check",children:"\u2713"}),(0,r.jsx)("span",{children:"\u0421\u0435\u0440\u0432\u0435\u0440"}),(0,r.jsx)("span",{className:"v",children:l.server})]}),(0,r.jsxs)("div",{className:"pr13-feat",children:[(0,r.jsx)("span",{className:"check",children:"\u2713"}),(0,r.jsx)("span",{children:"\u041F\u043E\u0434\u0434\u0435\u0440\u0436\u043A\u0430"}),(0,r.jsx)("span",{className:"v",children:l.sup})]}),l.crm&&(0,r.jsxs)("div",{className:"pr13-feat",children:[(0,r.jsx)("span",{className:"check",children:"\u2713"}),(0,r.jsx)("span",{children:"CRM-\u0438\u043D\u0442\u0435\u0433\u0440\u0430\u0446\u0438\u0438 (\u0411\u0438\u0442\u0440\u0438\u043A\u044124/AmoCRM) \xB7 Telegram/VK-\u0431\u043E\u0442\u044B"}),(0,r.jsx)("span",{className:"v",children:"\u0432\u043A\u043B"})]}),l.exp&&(0,r.jsxs)("div",{className:"pr13-feat",children:[(0,r.jsx)("span",{className:"check",children:"\u2713"}),(0,r.jsx)("span",{children:"\u042D\u043A\u0441\u043F\u043E\u0440\u0442 \u043A\u043E\u0434\u0430 (Next.js + Postgres)"}),(0,r.jsx)("span",{className:"v",children:"\u0432\u043A\u043B"})]}),l.runtime&&(0,r.jsxs)("div",{className:"pr13-feat",children:[(0,r.jsx)("span",{className:"check",children:"\u2713"}),(0,r.jsx)("span",{children:"1\u0421 \xB7 152-\u0424\u0417 \xB7 runtime-\u0431\u0438\u043B\u043B\u0438\u043D\u0433 \u0430\u0433\u0435\u043D\u0442\u043E\u0432"}),(0,r.jsx)("span",{className:"v",children:"\u0432\u043A\u043B"})]})]}),(0,r.jsxs)("div",{className:"pr13-result-cta",children:["\u0412\u044B\u0431\u0440\u0430\u0442\u044C ",l.name," \u2192"]}),(0,r.jsx)("div",{className:"pr13-result-foot",children:"7 \u0434\u043D\u0435\u0439 \u0431\u0435\u0441\u043F\u043B\u0430\u0442\u043D\u043E \xB7 \u0431\u0435\u0437 \u043A\u0430\u0440\u0442\u044B \xB7 \u043E\u0442\u043C\u0435\u043D\u0430 \u0432 1 \u043A\u043B\u0438\u043A"})]})]})]})]})}var us=[{q:"\u0427\u0435\u043C \u0432\u044B \u043E\u0442\u043B\u0438\u0447\u0430\u0435\u0442\u0435\u0441\u044C \u043E\u0442 Lovable / Bolt / v0?",a:"Lovable, Bolt \u0438 v0 \u0437\u0430\u0431\u043B\u043E\u043A\u0438\u0440\u043E\u0432\u0430\u043D\u044B \u0432 \u0420\u0424 \u0441 \u0444\u0435\u0432\u0440\u0430\u043B\u044F 2026 (Anthropic \u0440\u0435\u0436\u0435\u0442 VPN-IP). Omnia \u0440\u0430\u0431\u043E\u0442\u0430\u0435\u0442 \u043D\u0430 \u0440\u043E\u0441\u0441\u0438\u0439\u0441\u043A\u0438\u0445 \u043C\u043E\u0434\u0435\u043B\u044F\u0445 (YandexGPT, GigaChat, DeepSeek) \u0431\u0435\u0437 VPN, \u0432 \u0440\u0443\u0431\u043B\u044F\u0445, \u043F\u043E\u0434 152-\u0424\u0417. \u041F\u043B\u044E\u0441 \u043E\u0442\u0440\u0430\u0441\u043B\u0435\u0432\u044B\u0435 \u043F\u0430\u0442\u0442\u0435\u0440\u043D\u044B \u043F\u043E\u0434 \u0420\u0424-\u0440\u044B\u043D\u043E\u043A."},{q:"\u0410 \u0435\u0441\u043B\u0438 \u0418\u0418 \u043D\u0430\u043F\u0438\u0448\u0435\u0442 \u0435\u0440\u0443\u043D\u0434\u0443?",a:"\u041A\u0430\u0436\u0434\u044B\u0439 \u043F\u0440\u043E\u043C\u043F\u0442 = \u043D\u043E\u0432\u0430\u044F \u0432\u0435\u0440\u0441\u0438\u044F. \u041D\u0435 \u043D\u0440\u0430\u0432\u0438\u0442\u0441\u044F \u2014 \u043E\u0442\u043A\u0430\u0442 \u0437\u0430 1 \u0441\u0435\u043A\u0443\u043D\u0434\u0443. \u0418\u0441\u0442\u043E\u0440\u0438\u044F \u043F\u0440\u0430\u0432\u043E\u043A \u0431\u0435\u0441\u043A\u043E\u043D\u0435\u0447\u043D\u0430\u044F, \u0432\u0435\u0442\u043A\u0438 \u043A\u0430\u043A \u0432 git, \u043D\u043E \u0431\u0435\u0437 git."},{q:"\u041A\u0430\u043A\u0438\u0435 \u043C\u043E\u0434\u0435\u043B\u0438 \u043C\u043E\u0436\u043D\u043E \u0438\u0441\u043F\u043E\u043B\u044C\u0437\u043E\u0432\u0430\u0442\u044C?",a:"10 \u043C\u043E\u0434\u0435\u043B\u0435\u0439: Sonnet 4.5, Opus, Haiku, Gemini, DeepSeek, YandexGPT, GigaChat \u0438 \u0434\u0440. \u041F\u0435\u0440\u0435\u043A\u043B\u044E\u0447\u0430\u0442\u0435\u043B\u044C \u043F\u0440\u044F\u043C\u043E \u0432 \u0447\u0430\u0442\u0435 \u2014 \u0432\u044B\u0431\u0438\u0440\u0430\u0435\u0442\u0435 \u0431\u0430\u043B\u0430\u043D\u0441 \xAB\u043A\u0430\u0447\u0435\u0441\u0442\u0432\u043E / \u0446\u0435\u043D\u0430\xBB."},{q:"\u041C\u043E\u0436\u043D\u043E \u0443\u043D\u0435\u0441\u0442\u0438 \u043F\u0440\u043E\u0435\u043A\u0442 \u043D\u0430 \u0441\u0432\u043E\u0439 \u0441\u0435\u0440\u0432\u0435\u0440?",a:"\u0414\u0430, \u043D\u0430 \u0442\u0430\u0440\u0438\u0444\u0435 Pro \u0438 \u0432\u044B\u0448\u0435. \u042D\u043A\u0441\u043F\u043E\u0440\u0442 Next.js + Postgres + Dockerfile \u043E\u0434\u043D\u043E\u0439 \u043A\u043D\u043E\u043F\u043A\u043E\u0439. \u041D\u0438\u043A\u0430\u043A\u043E\u0433\u043E vendor lock-in \u2014 \u043F\u0440\u043E\u0435\u043A\u0442 \u043F\u043E\u043B\u043D\u043E\u0441\u0442\u044C\u044E \u0432\u0430\u0448."},{q:"\u0413\u0434\u0435 \u0445\u0440\u0430\u043D\u044F\u0442\u0441\u044F \u0434\u0430\u043D\u043D\u044B\u0435? 152-\u0424\u0417?",a:"\u0421\u0435\u0440\u0432\u0435\u0440\u044B \u0432 \u041C\u043E\u0441\u043A\u0432\u0435. \u0412\u0441\u0435 \u0442\u0430\u0440\u0438\u0444\u044B \u2014 152-\u0424\u0417 \u0438\u0437 \u043A\u043E\u0440\u043E\u0431\u043A\u0438. \u041D\u0430 Enterprise \u2014 \u0432\u044B\u0431\u043E\u0440 \u0440\u0435\u0433\u0438\u043E\u043D\u0430 (RU-1 \u041C\u043E\u0441\u043A\u0432\u0430 / RU-2 \u0421\u041F\u0431 / RU-3 \u041D\u043E\u0432\u043E\u0441\u0438\u0431\u0438\u0440\u0441\u043A)."},{q:"\u041A\u0430\u043A \u043E\u043F\u043B\u0430\u0447\u0438\u0432\u0430\u0442\u044C \u0431\u0435\u0437 \u0438\u043D\u043E\u0441\u0442\u0440\u0430\u043D\u043D\u043E\u0439 \u043A\u0430\u0440\u0442\u044B?",a:"\u041A\u0430\u0440\u0442\u0430 \u041C\u0418\u0420, \u0421\u0411\u041F, \u0441\u0447\u0451\u0442 \u0434\u043B\u044F \u044E\u0440\u043B\u0438\u0446. \u041D\u0438\u043A\u0430\u043A\u0438\u0445 VPN, \u043D\u0438\u043A\u0430\u043A\u0438\u0445 \u0432\u0430\u043B\u044E\u0442\u043D\u044B\u0445 \u043F\u0435\u0440\u0435\u0432\u043E\u0434\u043E\u0432. \u0414\u043E\u043C\u0435\u043D\u044B \u0442\u043E\u0436\u0435 \u043F\u043E\u043A\u0443\u043F\u0430\u044E\u0442\u0441\u044F \u043A\u0430\u0440\u0442\u043E\u0439 \u041C\u0418\u0420 \u043F\u0440\u044F\u043C\u043E \u0432 \u043F\u043B\u0430\u0442\u0444\u043E\u0440\u043C\u0435."},{q:"\u041F\u043E\u0434\u0445\u043E\u0434\u0438\u0442 \u0434\u043B\u044F production-\u043D\u0430\u0433\u0440\u0443\u0437\u043A\u0438?",a:"\u041D\u0430 Pro \u2014 \u0432\u044B\u0434\u0435\u043B\u0435\u043D\u043D\u044B\u0439 \u0441\u0435\u0440\u0432\u0435\u0440 + \u0447\u0430\u0442 \u0441 \u0438\u043D\u0436\u0435\u043D\u0435\u0440\u043E\u043C \u0432 \u0442\u0435\u0447\u0435\u043D\u0438\u0435 \u0447\u0430\u0441\u0430. \u041D\u0430 Enterprise \u2014 SLA 99.9% \u0441 \u043A\u043E\u043C\u043F\u0435\u043D\u0441\u0430\u0446\u0438\u0435\u0439, \u0431\u044D\u043A\u0430\u043F\u044B 4\xD7/\u0441\u0443\u0442\u043A\u0438, runtime-\u0431\u0438\u043B\u043B\u0438\u043D\u0433 \u0430\u0433\u0435\u043D\u0442\u043E\u0432 \u0438 \u043C\u0435\u0441\u0441\u0435\u043D\u0434\u0436\u0435\u0440\u043E\u0432."},{q:"\u0427\u0442\u043E \u0432\u0445\u043E\u0434\u0438\u0442 \u0432 \u0442\u0430\u0440\u0438\u0444 Pro?",a:"\u0411\u0435\u0437\u043B\u0438\u043C\u0438\u0442 \u043F\u0440\u043E\u0435\u043A\u0442\u043E\u0432, ~120 \u044D\u043A\u0432\u0438\u0432\u0430\u043B\u0435\u043D\u0442-\u0433\u0435\u043D\u0435\u0440\u0430\u0446\u0438\u0439/\u043C\u0435\u0441, \u0432\u044B\u0434\u0435\u043B\u0435\u043D\u043D\u044B\u0439 \u0441\u0435\u0440\u0432\u0435\u0440, \u0447\u0430\u0442-\u0431\u043E\u0442\u044B Telegram/VK, CRM (\u0411\u0438\u0442\u0440\u0438\u043A\u044124/AmoCRM), \u044D\u043A\u0441\u043F\u043E\u0440\u0442 \u043A\u043E\u0434\u0430. 8 990 \u20BD/\u043C\u0435\u0441. \u041F\u043E\u043A\u0440\u044B\u0432\u0430\u0435\u0442 \u0430\u0433\u0435\u043D\u0442\u0441\u0442\u0432\u043E 3\u201310 \u0447\u0435\u043B."}];function Qm(){let[e,t]=I([]),[n,a]=I(!1),[i,o]=I(null),s=ot(null);W(()=>{s.current&&(s.current.scrollTop=s.current.scrollHeight)},[e,n]);let l=f=>{let g=us[f];t(v=>[...v,{type:"q",text:g.q,qIdx:f}]),a(!0),o(g.a),setTimeout(()=>{t(v=>[...v,{type:"a",text:g.a}]),a(!1),o(null)},1100)},c=new Set(e.filter(f=>f.type==="q").map(f=>f.qIdx)),p=us.map((f,g)=>g).filter(f=>!c.has(f));return(0,r.jsxs)("section",{id:"faq",className:"fq13",children:[(0,r.jsx)("style",{children:`
        .fq13 { padding: 100px 24px; position: relative; }
        .fq13-inner { max-width: 880px; margin: 0 auto; }
        .fq13-head { text-align: center; margin-bottom: 40px; }
        .fq13-eyebrow {
          font-size: 12px; letter-spacing: 0.14em; text-transform: uppercase;
          color: var(--accent); font-weight: 700;
          display: inline-flex; align-items: center; gap: 10px; margin-bottom: 14px;
        }
        .fq13-eyebrow::before, .fq13-eyebrow::after {
          content:''; width: 18px; height: 1px; background: var(--accent);
        }
        .fq13-title {
          font-size: clamp(34px, 4.6vw, 60px);
          line-height: 1.0; letter-spacing: -0.04em;
          font-weight: 800; margin: 0 0 14px;
          background: linear-gradient(135deg, #fff, #aaa);
          -webkit-background-clip: text; background-clip: text;
          -webkit-text-fill-color: transparent;
        }
        .fq13-sub { font-size: 16px; color: var(--muted); }

        .fq13-chat {
          background: rgba(20,20,27,0.7); backdrop-filter: blur(20px);
          border: 1px solid rgba(255,255,255,0.08);
          border-radius: 22px;
          overflow: hidden;
          box-shadow: 0 40px 80px -30px rgba(0,0,0,0.6);
        }
        .fq13-bar {
          padding: 14px 20px;
          background: rgba(8,8,12,0.5);
          border-bottom: 1px solid rgba(255,255,255,0.06);
          display: flex; align-items: center; gap: 12px;
        }
        .fq13-bar .av {
          width: 30px; height: 30px; border-radius: 50%;
          background: linear-gradient(135deg, #7c5cff, #c66dff);
          display: inline-flex; align-items: center; justify-content: center;
          font-size: 14px;
        }
        .fq13-bar .nm { font-size: 14px; font-weight: 700; color: #fff; }
        .fq13-bar .st { font-size: 11px; color: #4cd9a4; display: flex; align-items: center; gap: 4px; }
        .fq13-bar .st .d { width: 6px; height: 6px; border-radius: 50%; background: #4cd9a4; animation: pulse-dot 1.4s infinite; }

        .fq13-messages {
          padding: 24px 20px;
          min-height: 320px; max-height: 420px;
          overflow-y: auto;
          display: flex; flex-direction: column; gap: 14px;
        }
        .fq13-messages::-webkit-scrollbar { width: 6px; }
        .fq13-messages::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 3px; }

        .fq13-msg {
          max-width: 78%;
          padding: 11px 16px;
          font-size: 14px; line-height: 1.5;
          animation: fq13-in .35s cubic-bezier(.2,.8,.2,1);
        }
        @keyframes fq13-in { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
        .fq13-msg.q {
          align-self: flex-end;
          background: linear-gradient(135deg, #7c5cff, #c66dff);
          color: #fff; font-weight: 500;
          border-radius: 16px 16px 4px 16px;
          box-shadow: 0 8px 20px -6px rgba(124,92,255,0.4);
        }
        .fq13-msg.a {
          align-self: flex-start;
          background: rgba(255,255,255,0.06);
          color: rgba(255,255,255,0.9);
          border-radius: 16px 16px 16px 4px;
          border: 1px solid rgba(255,255,255,0.06);
        }
        .fq13-greeting {
          align-self: flex-start;
          background: rgba(255,255,255,0.04);
          color: var(--muted);
          font-size: 13.5px;
          padding: 10px 14px;
          border-radius: 12px;
          font-style: italic;
        }

        .fq13-typing {
          align-self: flex-start;
          background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.06);
          padding: 12px 16px; border-radius: 16px 16px 16px 4px;
          display: inline-flex; align-items: center; gap: 5px;
        }
        .fq13-typing .d {
          width: 6px; height: 6px; border-radius: 50%; background: var(--accent);
          animation: fq13-typing 1.2s infinite ease-in-out both;
        }
        .fq13-typing .d:nth-child(1) { animation-delay: 0s; }
        .fq13-typing .d:nth-child(2) { animation-delay: 0.15s; }
        .fq13-typing .d:nth-child(3) { animation-delay: 0.3s; }
        @keyframes fq13-typing {
          0%, 80%, 100% { transform: scale(0.6); opacity: 0.5; }
          40% { transform: scale(1); opacity: 1; }
        }

        .fq13-suggest {
          padding: 14px 20px 20px;
          border-top: 1px solid rgba(255,255,255,0.06);
          display: flex; flex-wrap: wrap; gap: 8px;
        }
        .fq13-chip {
          padding: 9px 14px; border-radius: 999px;
          background: rgba(255,255,255,0.05);
          border: 1px solid rgba(255,255,255,0.1);
          color: rgba(255,255,255,0.85);
          font-size: 13px; font-weight: 500;
          cursor: pointer;
          transition: all .2s;
        }
        .fq13-chip:hover {
          background: rgba(124,92,255,0.15);
          border-color: rgba(124,92,255,0.4);
          color: #fff;
          transform: translateY(-1px);
        }
        .fq13-empty {
          padding: 12px 16px;
          color: var(--muted-2);
          font-size: 13px; text-align: center;
          font-style: italic;
        }
      `}),(0,r.jsxs)("div",{className:"fq13-inner",children:[(0,r.jsxs)("div",{className:"fq13-head",children:[(0,r.jsx)("div",{className:"fq13-eyebrow",children:"FAQ"}),(0,r.jsx)("h2",{className:"fq13-title",children:"\u0421\u043F\u0440\u043E\u0441\u0438\u0442\u0435 \u0431\u043E\u0442-\u043F\u043E\u043C\u043E\u0449\u043D\u0438\u043A\u0430"}),(0,r.jsx)("p",{className:"fq13-sub",children:"\u0412\u044B\u0431\u0435\u0440\u0438\u0442\u0435 \u0432\u043E\u043F\u0440\u043E\u0441 \u2014 \u043F\u043E\u043B\u0443\u0447\u0438\u0442\u0435 \u043E\u0442\u0432\u0435\u0442 \u0437\u0430 1 \u0441\u0435\u043A\u0443\u043D\u0434\u0443"})]}),(0,r.jsxs)("div",{className:"fq13-chat",children:[(0,r.jsxs)("div",{className:"fq13-bar",children:[(0,r.jsx)("div",{className:"av",children:"\u2299"}),(0,r.jsxs)("div",{children:[(0,r.jsx)("div",{className:"nm",children:"omnia-help"}),(0,r.jsxs)("div",{className:"st",children:[(0,r.jsx)("span",{className:"d"}),"\u043E\u043D\u043B\u0430\u0439\u043D \xB7 \u043E\u0442\u0432\u0435\u0447\u0430\u0435\u0442 \u0437\u0430 1\u0441"]})]})]}),(0,r.jsxs)("div",{className:"fq13-messages",ref:s,children:[e.length===0&&(0,r.jsx)("div",{className:"fq13-greeting",children:"\u041F\u0440\u0438\u0432\u0435\u0442! \u042F \u043E\u0442\u0432\u0435\u0447\u0443 \u043D\u0430 \u043B\u044E\u0431\u043E\u0439 \u0432\u043E\u043F\u0440\u043E\u0441 \u043F\u0440\u043E Omnia. \u0412\u044B\u0431\u0435\u0440\u0438\u0442\u0435 \u0432\u043E\u043F\u0440\u043E\u0441 \u0432\u043D\u0438\u0437\u0443 \u2193"}),e.map((f,g)=>(0,r.jsx)("div",{className:`fq13-msg ${f.type}`,children:f.text},g)),n&&(0,r.jsxs)("div",{className:"fq13-typing",children:[(0,r.jsx)("span",{className:"d"}),(0,r.jsx)("span",{className:"d"}),(0,r.jsx)("span",{className:"d"})]})]}),(0,r.jsx)("div",{className:"fq13-suggest",children:p.length===0?(0,r.jsx)("div",{className:"fq13-empty",children:"\u0412\u0441\u0435 \u0432\u043E\u043F\u0440\u043E\u0441\u044B \u0437\u0430\u0434\u0430\u043D\u044B. \u041E\u0441\u0442\u0430\u043B\u0438\u0441\u044C \u2014 \u043D\u0430\u043F\u0438\u0448\u0438\u0442\u0435 \u0432 \u0447\u0430\u0442-\u043F\u043E\u0434\u0434\u0435\u0440\u0436\u043A\u0443."}):p.map(f=>(0,r.jsx)("span",{className:"fq13-chip",onClick:()=>l(f),children:us[f].q},f))})]})]})]})}function Gm(){return(0,r.jsxs)("section",{id:"start",className:"fc13",children:[(0,r.jsx)("style",{children:`
        .fc13 { padding: 80px 24px 100px; position: relative; overflow: hidden; }
        .fc13-inner {
          max-width: 1240px; margin: 0 auto;
          background:
            radial-gradient(ellipse 70% 80% at 20% 0%, rgba(124,92,255,0.45), transparent 60%),
            radial-gradient(ellipse 70% 60% at 80% 100%, rgba(236,76,184,0.30), transparent 60%),
            linear-gradient(180deg, #14141b, #08080c);
          border: 1px solid rgba(255,255,255,0.08);
          border-radius: 32px;
          padding: 64px 56px;
          position: relative; overflow: hidden;
          box-shadow: 0 60px 120px -40px rgba(0,0,0,0.7);
        }
        .fc13-inner::before {
          content:''; position: absolute; inset: 0;
          background-image:
            linear-gradient(rgba(255,255,255,0.04) 1px, transparent 1px),
            linear-gradient(90deg, rgba(255,255,255,0.04) 1px, transparent 1px);
          background-size: 56px 56px;
          mask-image: radial-gradient(ellipse at center, black 30%, transparent 80%);
          pointer-events: none;
        }
        .fc13-grid {
          display: grid; grid-template-columns: 1.4fr 1fr; gap: 48px;
          align-items: center;
          position: relative;
        }
        .fc13-single {
          display: flex; flex-direction: column; align-items: center;
          text-align: center;
        }
        .fc13-center {
          max-width: 720px; position: relative;
          display: flex; flex-direction: column; align-items: center;
        }
        .fc13-center .fc13-title { text-align: center; }
        .fc13-center .fc13-sub { text-align: center; }
        .fc13-stats-row {
          display: grid; grid-template-columns: repeat(4, auto); gap: 48px;
          margin-top: 48px; padding-top: 32px;
          border-top: 1px solid rgba(255,255,255,0.07);
        }
        .fc13-stats-row .fc13-stat .v { font-size: 32px; }
        @media (max-width: 720px) {
          .fc13-stats-row { grid-template-columns: 1fr 1fr; gap: 28px; }
        }
        .fc13-left {}
        .fc13-eyebrow {
          display: inline-flex; align-items: center; gap: 9px;
          padding: 7px 14px 7px 9px; border-radius: 999px;
          background: rgba(255,255,255,0.06); backdrop-filter: blur(10px);
          border: 1px solid rgba(255,255,255,0.1);
          font-size: 13px; color: rgba(255,255,255,0.85); font-weight: 600;
          margin-bottom: 22px;
        }
        .fc13-eyebrow .d {
          width: 7px; height: 7px; border-radius: 50%; background: #4cd9a4;
          box-shadow: 0 0 8px #4cd9a4;
          animation: pulse-dot 1.4s infinite;
        }
        .fc13-title {
          font-size: clamp(38px, 5.6vw, 72px);
          line-height: 0.96; letter-spacing: -0.05em;
          font-weight: 900; margin: 0 0 16px;
          color: #fff;
          text-wrap: balance;
        }
        .fc13-title .grad {
          background: linear-gradient(105deg, #7c5cff, #c66dff, #fff, #7c5cff);
          background-size: 200% 100%;
          -webkit-background-clip: text; background-clip: text;
          -webkit-text-fill-color: transparent;
          animation: hv6-shiny 5s linear infinite;
        }
        .fc13-sub {
          font-size: 18px; line-height: 1.55; color: rgba(255,255,255,0.65);
          max-width: 48ch; margin: 0 0 28px;
        }
        .fc13-cta-row { display: flex; flex-wrap: wrap; gap: 14px; align-items: center; }
        .fc13-cta-primary {
          padding: 16px 28px; border-radius: 999px;
          background: linear-gradient(135deg, #7c5cff, #c66dff);
          color: #fff; font-weight: 700; font-size: 16px;
          display: inline-flex; align-items: center; gap: 9px;
          cursor: pointer; border: 0;
          box-shadow: 0 20px 50px -16px rgba(124,92,255,0.7), 0 0 0 1px rgba(255,255,255,0.1) inset;
          transition: transform .2s, box-shadow .2s;
        }
        .fc13-cta-primary:hover {
          transform: translateY(-2px);
          box-shadow: 0 28px 60px -16px rgba(124,92,255,0.85);
        }
        .fc13-cta-ghost {
          padding: 14px 22px; border-radius: 999px;
          background: rgba(255,255,255,0.04); backdrop-filter: blur(10px);
          border: 1px solid rgba(255,255,255,0.1);
          color: rgba(255,255,255,0.85); font-weight: 600; font-size: 15px;
        }

        /* RIGHT: Live activity panel */
        .fc13-live {
          background: rgba(8,8,12,0.7); backdrop-filter: blur(14px);
          border: 1px solid rgba(76,217,164,0.25);
          border-radius: 18px; padding: 22px;
          display: flex; flex-direction: column; gap: 18px;
        }
        .fc13-live-head {
          display: flex; align-items: center; justify-content: space-between;
        }
        .fc13-live-head .t {
          font-size: 14px; font-weight: 700; color: #fff;
          display: inline-flex; align-items: center; gap: 8px;
        }
        .fc13-live-head .d {
          width: 8px; height: 8px; border-radius: 50%; background: #4cd9a4;
          box-shadow: 0 0 10px #4cd9a4;
          animation: pulse-dot 1.4s infinite;
        }
        .fc13-live-head .ct { font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--muted); }

        .fc13-feed {
          display: flex; flex-direction: column; gap: 10px;
          min-height: 160px;
        }
        .fc13-evt {
          padding: 10px 12px; border-radius: 10px;
          background: rgba(255,255,255,0.03);
          border: 1px solid rgba(255,255,255,0.04);
          display: flex; align-items: center; gap: 10px;
          font-size: 12.5px;
        }
        .fc13-evt .av {
          width: 22px; height: 22px; border-radius: 50%;
          background: linear-gradient(135deg, var(--av-c), color-mix(in srgb, var(--av-c), white 30%));
          color: #fff; display: inline-flex; align-items: center; justify-content: center;
          font-size: 10px; font-weight: 800;
          flex-shrink: 0;
        }
        .fc13-evt .text { color: rgba(255,255,255,0.85); flex: 1; }
        .fc13-evt .text b { color: #fff; font-weight: 700; }
        .fc13-evt .time {
          font-family: 'JetBrains Mono', monospace; font-size: 10.5px;
          color: var(--muted-2);
        }

        .fc13-stats {
          display: grid; grid-template-columns: 1fr 1fr; gap: 10px;
          padding-top: 14px;
          border-top: 1px solid rgba(255,255,255,0.06);
        }
        .fc13-stat .v {
          font-size: 26px; font-weight: 900; color: #fff;
          font-variant-numeric: tabular-nums;
          line-height: 1;
          background: linear-gradient(135deg, #7c5cff, #c66dff);
          -webkit-background-clip: text; background-clip: text;
          -webkit-text-fill-color: transparent;
        }
        .fc13-stat .l {
          font-size: 11.5px; color: var(--muted-2); margin-top: 4px;
          font-family: 'JetBrains Mono', monospace; letter-spacing: 0.04em;
        }

        @media (max-width: 880px) {
          .fc13-inner { padding: 40px 24px; }
          .fc13-grid { grid-template-columns: 1fr; gap: 32px; }
        }
      `}),(0,r.jsx)("div",{className:"fc13-inner fc13-single",children:(0,r.jsxs)("div",{className:"fc13-center",children:[(0,r.jsxs)("div",{className:"fc13-eyebrow",children:[(0,r.jsx)("span",{className:"d"}),"7 \u0434\u043D\u0435\u0439 \u0431\u0435\u0441\u043F\u043B\u0430\u0442\u043D\u043E \xB7 \u0431\u0435\u0437 \u043A\u0430\u0440\u0442\u044B"]}),(0,r.jsxs)("h2",{className:"fc13-title",children:["\u041F\u043E\u0441\u0442\u0440\u043E\u0439\u0442\u0435 \u0441\u0432\u043E\u0439",(0,r.jsx)("br",{}),(0,r.jsx)("span",{className:"grad",children:"\u0437\u0430 \u043E\u0434\u0438\u043D \u0432\u0435\u0447\u0435\u0440"})]}),(0,r.jsx)("p",{className:"fc13-sub",children:"\u041E\u043F\u0438\u0448\u0438\u0442\u0435 \u0438\u0434\u0435\u044E \u2014 Omnia \u0441\u043E\u0431\u0435\u0440\u0451\u0442 \u0440\u0430\u0431\u043E\u0447\u0438\u0439 \u0441\u0430\u0439\u0442, \u0431\u043E\u0442\u0430 \u0438\u043B\u0438 \u043A\u0430\u0431\u0438\u043D\u0435\u0442 \u0441 \u0434\u043E\u043C\u0435\u043D\u043E\u043C, \u0445\u043E\u0441\u0442\u0438\u043D\u0433\u043E\u043C \u0438 SSL. \u0420\u043E\u0441\u0441\u0438\u0439\u0441\u043A\u0438\u0439 \u0441\u0442\u0435\u043A, \u0431\u0435\u0437 VPN, \u0432\xA0\u0440\u0443\u0431\u043B\u044F\u0445."}),(0,r.jsxs)("div",{className:"fc13-cta-row",children:[(0,r.jsx)(Hf,{strength:.22,children:(0,r.jsx)("button",{className:"fc13-cta-primary",children:"\u041D\u0430\u0447\u0430\u0442\u044C \u0431\u0435\u0441\u043F\u043B\u0430\u0442\u043D\u043E \u2192"})}),(0,r.jsx)("a",{className:"fc13-cta-ghost",children:"\u041F\u043E\u0433\u043E\u0432\u043E\u0440\u0438\u0442\u044C \u0441 \u043A\u043E\u043C\u0430\u043D\u0434\u043E\u0439"})]}),(0,r.jsxs)("div",{className:"fc13-stats fc13-stats-row",children:[(0,r.jsxs)("div",{className:"fc13-stat",children:[(0,r.jsx)("div",{className:"v",children:(0,r.jsx)(ps,{val:1247,label:""})}),(0,r.jsx)("div",{className:"l",children:"\u043F\u0440\u043E\u0435\u043A\u0442\u043E\u0432 \u043F\u043E\u0434\u043D\u044F\u0442\u043E"})]}),(0,r.jsxs)("div",{className:"fc13-stat",children:[(0,r.jsxs)("div",{className:"v",children:[(0,r.jsx)(ps,{val:12,label:""})," \u043C\u0438\u043D"]}),(0,r.jsx)("div",{className:"l",children:"\u043C\u0435\u0434\u0438\u0430\u043D\u0430"})]}),(0,r.jsxs)("div",{className:"fc13-stat",children:[(0,r.jsxs)("div",{className:"v",children:[(0,r.jsx)(ps,{val:99,dec:0,label:""}),".9%"]}),(0,r.jsx)("div",{className:"l",children:"uptime \xB7 SLA"})]}),(0,r.jsxs)("div",{className:"fc13-stat",children:[(0,r.jsx)("div",{className:"v",children:"152-\u0424\u0417"}),(0,r.jsx)("div",{className:"l",children:"\u0441\u0435\u0440\u0432\u0435\u0440\u044B \u0432 \u0420\u0424"})]})]})]})})]})}var Xm={direction:"editorial",accent:"#7c5cff",theme:"dark",showPreview:!0};function dd(){let[e,t]=Wf(Xm),[n,a]=I("month");return W(()=>{document.body.setAttribute("data-direction",e.direction||"editorial")},[e.direction]),W(()=>{document.body.setAttribute("data-theme",e.theme||"dark")},[e.theme]),W(()=>{let i=e.accent||"#7c5cff",o=document.documentElement;o.style.setProperty("--accent",i),o.style.setProperty("--accent-2",Km(i,-10)),o.style.setProperty("--accent-soft",ad(i,.08)),o.style.setProperty("--accent-line",ad(i,.22))},[e.accent]),(0,r.jsxs)(r.Fragment,{children:[(0,r.jsx)(Yf,{}),(0,r.jsx)(Em,{}),(0,r.jsx)(Cm,{}),(0,r.jsx)(cm,{}),(0,r.jsx)(Tm,{}),(0,r.jsx)($m,{}),(0,r.jsx)(Hm,{}),(0,r.jsx)(qm,{}),(0,r.jsx)(Qm,{}),(0,r.jsx)(Gm,{}),(0,r.jsx)(Pm,{})]})}function ad(e,t){let n=e.replace("#",""),a=parseInt(n.slice(0,2),16),i=parseInt(n.slice(2,4),16),o=parseInt(n.slice(4,6),16);return`rgba(${a},${i},${o},${t})`}function Km(e,t){let n=e.replace("#",""),a=parseInt(n.slice(0,2),16),i=parseInt(n.slice(2,4),16),o=parseInt(n.slice(4,6),16),s=1+t/100;return a=Math.max(0,Math.min(255,Math.round(a*s))),i=Math.max(0,Math.min(255,Math.round(i*s))),o=Math.max(0,Math.min(255,Math.round(o*s))),"#"+[a,i,o].map(l=>l.toString(16).padStart(2,"0")).join("")}od.createRoot(document.getElementById("root")).render((0,r.jsx)(dd,{}));var fs=document.getElementById("root");fs?(fs.innerHTML="",od.createRoot(fs).render(ms.default.createElement(dd))):console.error("[omnia-landing] #root not found");})();
/*! Bundled license information:

react/cjs/react.production.min.js:
  (**
   * @license React
   * react.production.min.js
   *
   * Copyright (c) Facebook, Inc. and its affiliates.
   *
   * This source code is licensed under the MIT license found in the
   * LICENSE file in the root directory of this source tree.
   *)

scheduler/cjs/scheduler.production.min.js:
  (**
   * @license React
   * scheduler.production.min.js
   *
   * Copyright (c) Facebook, Inc. and its affiliates.
   *
   * This source code is licensed under the MIT license found in the
   * LICENSE file in the root directory of this source tree.
   *)

react-dom/cjs/react-dom.production.min.js:
  (**
   * @license React
   * react-dom.production.min.js
   *
   * Copyright (c) Facebook, Inc. and its affiliates.
   *
   * This source code is licensed under the MIT license found in the
   * LICENSE file in the root directory of this source tree.
   *)

react/cjs/react-jsx-runtime.production.min.js:
  (**
   * @license React
   * react-jsx-runtime.production.min.js
   *
   * Copyright (c) Facebook, Inc. and its affiliates.
   *
   * This source code is licensed under the MIT license found in the
   * LICENSE file in the root directory of this source tree.
   *)
*/
