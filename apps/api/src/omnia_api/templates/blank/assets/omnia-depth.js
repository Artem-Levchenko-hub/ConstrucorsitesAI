/*
 * Omnia Depth v1 — managed, dependency-free interactive 3D.
 *
 * `data-omnia-depth="sculpture|orbital|product"` mounts a ray-marched WebGL
 * scene. `data-omnia-depth="media"` turns authored `[data-depth-layer]` media
 * into a restrained perspective stack.  Both modes are pointer-responsive,
 * pause offscreen, cap DPR, and preserve a complete CSS/markup fallback when
 * WebGL, JavaScript, hover, or motion are unavailable.
 */
(function () {
  "use strict";

  var reduce = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var hover = !window.matchMedia ||
    window.matchMedia("(hover: hover) and (pointer: fine)").matches;

  function installStyles() {
    if (document.getElementById("omnia-depth-styles")) return;
    var style = document.createElement("style");
    style.id = "omnia-depth-styles";
    style.textContent = [
      ".omnia-depth{position:relative;overflow:hidden;isolation:isolate;",
      "--depth-a:#101827;--depth-b:#267985;--depth-c:#ed9940;--depth-bg:#05080d;",
      "background:radial-gradient(circle at 25% 20%,var(--depth-b),transparent 52%),var(--depth-bg)}",
      ".omnia-depth>.omnia-depth-canvas{position:absolute;inset:0;width:100%;height:100%;",
      "display:block;z-index:0;opacity:0;transition:opacity .65s ease;pointer-events:none}",
      ".omnia-depth>.omnia-depth-canvas.is-live{opacity:1}",
      ".omnia-depth>[data-depth-content],.omnia-depth>.omnia-shader-over{position:relative;z-index:2}",
      ".omnia-depth-media{perspective:1100px;transform-style:preserve-3d;--depth-x:0;--depth-y:0}",
      ".omnia-depth-media [data-depth-layer]{transform:perspective(1100px) ",
      "translate3d(calc(var(--depth-x)*var(--depth-z)*10px),calc(var(--depth-y)*var(--depth-z)*10px),",
      "calc(var(--depth-z)*9px)) rotateX(calc(var(--depth-y)*-1.4deg)) rotateY(calc(var(--depth-x)*1.8deg));",
      "transition:transform .18s ease-out;will-change:transform}",
      "@media(prefers-reduced-motion:reduce){.omnia-depth>.omnia-depth-canvas{display:none}",
      ".omnia-depth-media [data-depth-layer]{transform:none!important;transition:none!important}}"
    ].join("");
    document.head.appendChild(style);
  }

  function hex(value) {
    var text = (value || "").trim().replace("#", "");
    if (text.length === 3) {
      text = text.replace(/(.)/g, "$1$1");
    }
    if (!/^[0-9a-f]{6}$/i.test(text)) return null;
    var number = parseInt(text, 16);
    return [
      ((number >> 16) & 255) / 255,
      ((number >> 8) & 255) / 255,
      (number & 255) / 255
    ];
  }

  function palette(host) {
    var colors = [];
    var value = host.getAttribute("data-depth-colors") ||
      host.getAttribute("data-omnia-colors") || "";
    value.split(",").forEach(function (item) {
      var color = hex(item);
      if (color) colors.push(color);
    });
    if (colors.length < 2) {
      var styles = getComputedStyle(host);
      ["--depth-a", "--depth-b", "--depth-c", "--depth-bg"].forEach(function (name) {
        var color = hex(styles.getPropertyValue(name));
        if (color) colors.push(color);
      });
    }
    var defaults = [
      [0.06, 0.08, 0.12],
      [0.15, 0.48, 0.55],
      [0.93, 0.60, 0.25],
      [0.02, 0.03, 0.05]
    ];
    while (colors.length < 4) colors.push(defaults[colors.length]);
    return colors.slice(0, 4);
  }

  function mountMedia(host) {
    host.classList.add("omnia-depth-media");
    var layers = [].slice.call(host.querySelectorAll("[data-depth-layer]"));
    if (!layers.length) return;
    layers.forEach(function (layer, index) {
      var declared = parseFloat(layer.getAttribute("data-depth-layer"));
      layer.style.setProperty("--depth-z", String(isNaN(declared) ? index + 1 : declared));
    });
    if (reduce || !hover) return;
    var target = [0, 0], current = [0, 0], raf = 0;
    function frame() {
      current[0] += (target[0] - current[0]) * 0.09;
      current[1] += (target[1] - current[1]) * 0.09;
      host.style.setProperty("--depth-x", current[0].toFixed(3));
      host.style.setProperty("--depth-y", current[1].toFixed(3));
      if (Math.abs(target[0] - current[0]) + Math.abs(target[1] - current[1]) > 0.002) {
        raf = requestAnimationFrame(frame);
      } else {
        raf = 0;
      }
    }
    function schedule() {
      if (!raf) raf = requestAnimationFrame(frame);
    }
    host.addEventListener("pointermove", function (event) {
      var rect = host.getBoundingClientRect();
      target = [
        ((event.clientX - rect.left) / Math.max(rect.width, 1)) * 2 - 1,
        ((event.clientY - rect.top) / Math.max(rect.height, 1)) * 2 - 1
      ];
      schedule();
    }, { passive: true });
    host.addEventListener("pointerleave", function () {
      target = [0, 0];
      schedule();
    }, { passive: true });
  }

  function mountWebGL(host) {
    if (reduce || !window.WebGLRenderingContext) return;
    var canvas = document.createElement("canvas");
    canvas.className = "omnia-depth-canvas";
    canvas.setAttribute("aria-hidden", "true");
    var gl = canvas.getContext("webgl", {
      alpha: true,
      antialias: false,
      depth: false,
      powerPreference: "high-performance"
    });
    if (!gl) return;

    var vertex = "attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}";
    var fragment = [
      "precision highp float;",
      "uniform vec2 r,m;uniform float t,v;uniform vec3 a,b,c,k;",
      "mat2 q(float x){float s=sin(x),d=cos(x);return mat2(d,-s,s,d);}",
      "float sdBox(vec3 p,vec3 z){vec3 x=abs(p)-z;return length(max(x,0.))+min(max(x.x,max(x.y,x.z)),0.);}",
      "float sdTorus(vec3 p,vec2 z){vec2 x=vec2(length(p.xz)-z.x,p.y);return length(x)-z.y;}",
      "float sm(float x,float y,float z){float h=clamp(.5+.5*(y-x)/z,0.,1.);return mix(y,x,h)-z*h*(1.-h);}",
      "float map(vec3 p){",
      " p.xy*=q(t*.18+(m.x-.5)*.55);p.yz*=q(t*.12+(m.y-.5)*.42);",
      " float s=length(p)-.78;",
      " float o=sdTorus(p.xzy,vec2(.92,.16));",
      " float d=sm(s,o,.28);",
      " if(v>1.5){vec3 z=p;z.xz*=q(t*.24);d=sm(sdBox(z,vec3(.62,.62,.62))-.08,o,.22);}",
      " if(v>.5&&v<1.5){vec3 z=p;z.xy*=q(-t*.3);d=sm(length(z-vec3(.52,.08,0.))-.48,length(z+vec3(.52,.08,0.))-.48,.26);d=sm(d,o,.18);}",
      " return d;",
      "}",
      "vec3 normal(vec3 p){vec2 e=vec2(.002,0.);return normalize(vec3(map(p+e.xyy)-map(p-e.xyy),map(p+e.yxy)-map(p-e.yxy),map(p+e.yyx)-map(p-e.yyx)));}",
      "void main(){",
      " vec2 u=(2.*gl_FragCoord.xy-r.xy)/min(r.x,r.y);",
      " vec2 orbit=(m-.5)*vec2(.55,.38);",
      " vec3 ro=vec3(orbit.x,orbit.y,3.35),rd=normalize(vec3(u,-1.9));",
      " float z=0.,hit=0.;vec3 p;",
      " for(int i=0;i<64;i++){p=ro+rd*z;float d=map(p);if(abs(d)<.0015){hit=1.;break;}z+=d*.72;if(z>7.)break;}",
      " vec3 bg=mix(k,a*.35+.04,.32+.30*max(u.y,0.));",
      " float halo=exp(-1.8*length(u-vec2((m.x-.5)*.16,(m.y-.5)*.12)));bg+=b*.11*halo;",
      " if(hit<.5){gl_FragColor=vec4(bg,1.);return;}",
      " vec3 n=normal(p),l=normalize(vec3(-.55,.75,1.2)),h=normalize(l-rd);",
      " float dif=max(dot(n,l),0.),spec=pow(max(dot(n,h),0.),48.),rim=pow(1.-max(dot(n,-rd),0.),2.4);",
      " float bands=.5+.5*sin(5.5*p.y+2.2*p.x+t*.35);",
      " vec3 mat=mix(a,b,.28+.42*bands);mat=mix(mat,c,.30*rim);",
      " vec3 col=mat*(.18+.82*dif)+spec*c*1.3+rim*b*.65;",
      " col=mix(col,bg,1.-exp(-.025*z*z));",
      " gl_FragColor=vec4(pow(col,vec3(.92)),1.);",
      "}"
    ].join("");

    function shader(type, source) {
      var item = gl.createShader(type);
      gl.shaderSource(item, source);
      gl.compileShader(item);
      return gl.getShaderParameter(item, gl.COMPILE_STATUS) ? item : null;
    }
    var vs = shader(gl.VERTEX_SHADER, vertex);
    var fs = shader(gl.FRAGMENT_SHADER, fragment);
    if (!vs || !fs) return;
    var program = gl.createProgram();
    gl.attachShader(program, vs);
    gl.attachShader(program, fs);
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return;
    gl.useProgram(program);

    var buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    var position = gl.getAttribLocation(program, "p");
    gl.enableVertexAttribArray(position);
    gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
    host.insertBefore(canvas, host.firstChild);

    var resolution = gl.getUniformLocation(program, "r");
    var mouse = gl.getUniformLocation(program, "m");
    var time = gl.getUniformLocation(program, "t");
    var variant = gl.getUniformLocation(program, "v");
    var colors = palette(host);
    ["a", "b", "c", "k"].forEach(function (name, index) {
      gl.uniform3fv(gl.getUniformLocation(program, name), colors[index]);
    });
    var mode = host.getAttribute("data-omnia-depth") || "sculpture";
    gl.uniform1f(variant, mode === "orbital" ? 1 : mode === "product" ? 2 : 0);

    var dpr = Math.min(window.devicePixelRatio || 1, 1.6);
    function resize() {
      var width = Math.max(host.clientWidth, 1);
      var height = Math.max(host.clientHeight, 1);
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      gl.viewport(0, 0, canvas.width, canvas.height);
    }
    resize();
    var observer = window.ResizeObserver ? new ResizeObserver(resize) : null;
    if (observer) observer.observe(host);
    else window.addEventListener("resize", resize, { passive: true });

    var target = [.5, .5], current = [.5, .5], running = false, start = 0;
    if (hover) {
      host.addEventListener("pointermove", function (event) {
        var rect = host.getBoundingClientRect();
        target = [
          (event.clientX - rect.left) / Math.max(rect.width, 1),
          1 - (event.clientY - rect.top) / Math.max(rect.height, 1)
        ];
      }, { passive: true });
      host.addEventListener("pointerleave", function () { target = [.5, .5]; }, { passive: true });
    }
    function frame(now) {
      if (!start) start = now;
      current[0] += (target[0] - current[0]) * .055;
      current[1] += (target[1] - current[1]) * .055;
      gl.uniform2f(resolution, canvas.width, canvas.height);
      gl.uniform2f(mouse, current[0], current[1]);
      gl.uniform1f(time, (now - start) / 1000);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      canvas.classList.add("is-live");
      if (running) requestAnimationFrame(frame);
    }
    function play() {
      if (running) return;
      running = true;
      requestAnimationFrame(frame);
    }
    function pause() { running = false; }
    if (window.IntersectionObserver) {
      new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) { if (entry.isIntersecting) play(); else pause(); });
      }, { rootMargin: "120px" }).observe(host);
    } else {
      play();
    }
  }

  function mount(host) {
    if (host.getAttribute("data-omnia-depth-bound") === "1") return;
    host.setAttribute("data-omnia-depth-bound", "1");
    host.classList.add("omnia-depth");
    if ((host.getAttribute("data-omnia-depth") || "").toLowerCase() === "media") {
      mountMedia(host);
    } else {
      mountWebGL(host);
    }
  }

  function scan(root) {
    installStyles();
    [].slice.call((root || document).querySelectorAll("[data-omnia-depth]")).forEach(mount);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { scan(document); });
  } else {
    scan(document);
  }
  window.__omniaDepthScan = scan;
}());
