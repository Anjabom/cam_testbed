/* 그리기 — 왜곡보정과 BEV 워프를 GPU 에서 한다.
 *
 * ★식은 geom.js 와 같은 것을 쓴다★ 아래 셰이더의 세 줄(정규화 → 왜곡 →
 * 원본 픽셀)은 `Geom.undistortMapPoint` 와 글자 그대로 같은 계산이다.
 * 여기가 갈라지면 ★그림만 거짓말을 한다★ — 내보내는 숫자는 geom.js 가 내므로
 * 값은 맞는데 화면이 다른 자리를 보여 주는, 제일 알아채기 어려운 고장이 된다.
 * 그래서 셰이더를 고칠 일이 생기면 geom.js 의 같은 함수도 같이 고친다.
 *
 * ★CPU 로 픽셀을 돌지 않는다★ 1920×1080 을 자바스크립트로 훑으면 드래그 한 번에
 * 수백 ms 가 든다. 「직접 맞추는」 화면에서 그 지연은 도구를 못 쓰게 만든다.
 */
(function (root) {
  'use strict';

  var VERT =
    'attribute vec2 aPos;' +
    'attribute vec2 aUV;' +
    'varying vec2 vUV;' +
    'void main(){ vUV = aUV; gl_Position = vec4(aPos, 0.0, 1.0); }';

  //  출력 픽셀 → (H) → 보정·리사이즈된 영상 → (ROI 역) → 보정 전체
  //   → (왜곡) → 원본 픽셀. geom.js 의 bevToSource 와 같은 순서다.
  var FRAG =
    'precision highp float;' +
    'uniform sampler2D uTex;' +
    'uniform vec2 uOutSize;' +      // 출력 캔버스 크기 [px]
    'uniform mat3 uH;' +            // 출력 픽셀 → 보정·리사이즈된 영상 좌표
    'uniform vec4 uRoi;' +          // x y w h
    'uniform vec2 uUndSize;' +      // 보정 전체 크기 (= 카메라 size)
    'uniform vec4 uK;' +            // fx fy cx cy (원본)
    'uniform vec4 uNewK;' +         // fx fy cx cy (보정 후)
    'uniform vec3 uKr;' +           // k1 k2 k3
    'uniform vec2 uTan;' +          // p1 p2
    'varying vec2 vUV;' +
    'void main(){' +
    '  vec3 hp = uH * vec3(vUV * uOutSize, 1.0);' +
    '  vec2 u = hp.xy / hp.z;' +
    '  vec2 s = (u + 0.5) * (uRoi.zw / uUndSize) - 0.5 + uRoi.xy;' +
    '  vec2 n = (s - uNewK.zw) / uNewK.xy;' +
    '  float r2 = dot(n, n);' +
    '  float kr = 1.0 + ((uKr.z * r2 + uKr.y) * r2 + uKr.x) * r2;' +
    '  vec2 d = vec2(' +
    '    n.x * kr + 2.0 * uTan.x * n.x * n.y + uTan.y * (r2 + 2.0 * n.x * n.x),' +
    '    n.y * kr + uTan.x * (r2 + 2.0 * n.y * n.y) + 2.0 * uTan.y * n.x * n.y);' +
    '  vec2 src = d * uK.xy + uK.zw;' +
    //  ★uUndSize 로 나눈다 — 텍스처 실제 해상도가 아니다★ 노드는 프레임을 먼저
    //  카메라 크기로 resize 한 뒤 보정한다(tb.geometry.Undistorter). 그래서 원본
    //  좌표는 언제나 「카메라 크기」 공간의 값이고, 1280×720 영상을 열어도
    //  1920×1080 카메라로 재면 노드와 같은 자리를 본다.
    '  vec2 t = src / uUndSize;' +
    //  원본 밖 — 노드도 여기서는 검은 화면을 본다. 회색으로 두어
    //  「사각형이 화면 밖으로 나갔다」가 눈에 띄게 한다.
    '  if (t.x < 0.0 || t.x > 1.0 || t.y < 0.0 || t.y > 1.0) {' +
    '    gl_FragColor = vec4(0.09, 0.09, 0.10, 1.0); return; }' +
    '  gl_FragColor = texture2D(uTex, t);' +
    '}';

  function compile(gl, type, src) {
    var sh = gl.createShader(type);
    gl.shaderSource(sh, src);
    gl.compileShader(sh);
    if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
      throw new Error('셰이더 컴파일 실패: ' + gl.getShaderInfoLog(sh));
    }
    return sh;
  }

  function create() {
    var canvas = document.createElement('canvas');
    var gl = canvas.getContext('webgl', { preserveDrawingBuffer: true })
          || canvas.getContext('experimental-webgl', { preserveDrawingBuffer: true });
    if (!gl) return null;

    var prog = gl.createProgram();
    gl.attachShader(prog, compile(gl, gl.VERTEX_SHADER, VERT));
    gl.attachShader(prog, compile(gl, gl.FRAGMENT_SHADER, FRAG));
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      throw new Error('셰이더 링크 실패: ' + gl.getProgramInfoLog(prog));
    }
    gl.useProgram(prog);

    //  화면을 덮는 사각형 둘. uv 는 ★y 를 뒤집어★ (0,0) 이 왼쪽 위가 되게 둔다
    //  — texImage2D 는 그림의 첫 줄(위)을 t=0 에 올리므로 이렇게 맞아떨어진다.
    var buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([
      -1, -1, 0, 1, 1, -1, 1, 1, -1, 1, 0, 0, 1, 1, 1, 0
    ]), gl.STATIC_DRAW);
    var aPos = gl.getAttribLocation(prog, 'aPos');
    var aUV = gl.getAttribLocation(prog, 'aUV');
    gl.enableVertexAttribArray(aPos);
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 16, 0);
    gl.enableVertexAttribArray(aUV);
    gl.vertexAttribPointer(aUV, 2, gl.FLOAT, false, 16, 8);

    var tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    //  ★NPOT 텍스처★ 영상은 2의 거듭제곱이 아니다 — CLAMP + LINEAR 만 쓸 수 있고
    //  밉맵을 만들면 그 순간 텍스처가 「불완전」해져 새까맣게 나온다.
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);

    var U = {};
    ['uTex', 'uOutSize', 'uH', 'uRoi', 'uUndSize', 'uK', 'uNewK', 'uKr', 'uTan'
    ].forEach(function (n) { U[n] = gl.getUniformLocation(prog, n); });

    return {
      canvas: canvas,

      //  영상/사진 한 장을 GPU 로 올린다. 영상은 프레임마다 다시 부른다.
      setSource: function (el) {
        gl.bindTexture(gl.TEXTURE_2D, tex);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, el);
      },

      //  cal: Geom.makeCal 의 결과. H: 출력 픽셀 → 보정·리사이즈 좌표(행 우선 3x3).
      render: function (outW, outH, H, cal) {
        canvas.width = outW;
        canvas.height = outH;
        gl.viewport(0, 0, outW, outH);
        gl.uniform1i(U.uTex, 0);
        gl.uniform2f(U.uOutSize, outW, outH);
        //  ★열 우선으로 넣는다★ WebGL1 은 transpose=true 를 받지 않는다
        gl.uniformMatrix3fv(U.uH, false, new Float32Array([
          H[0][0], H[1][0], H[2][0],
          H[0][1], H[1][1], H[2][1],
          H[0][2], H[1][2], H[2][2]
        ]));
        gl.uniform4f(U.uRoi, cal.roi[0], cal.roi[1], cal.roi[2], cal.roi[3]);
        gl.uniform2f(U.uUndSize, cal.size[0], cal.size[1]);
        gl.uniform4f(U.uK, cal.K[0], cal.K[1], cal.K[2], cal.K[3]);
        gl.uniform4f(U.uNewK, cal.newK[0], cal.newK[1], cal.newK[2], cal.newK[3]);
        var d = window.Geom.padD(cal.D);
        gl.uniform3f(U.uKr, d[0], d[1], d[4]);
        gl.uniform2f(U.uTan, d[2], d[3]);
        gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
        return canvas;
      }
    };
  }

  root.Render = { create: create };
})(typeof window !== 'undefined' ? window : globalThis);
