/* mp4v(MPEG-4 Part 2) 디코더 — 브라우저가 못 여는 코덱을 wasm 으로 연다.
 *
 * ★왜 이것이 있나★ 이 기계의 녹화는 전부 cv2 기본 코덱 mp4v 이고, 브라우저는
 * mp4 안에서 H.264·AV1 만 받는다. github.io 페이지는 서버가 없으니 cv2 에
 * 기댈 수도 없다 — 그래서 FFmpeg 의 mpeg4 디코더 ★하나만★ 떼어 굽는다.
 *
 * ★컨테이너는 여기서 풀지 않는다★ mp4 상자 읽기는 web/mp4demux.js 가 하고,
 * 여기는 「패킷 하나 → 그림 한 장」만 한다. libavformat 을 넣으면 파일 전체를
 * wasm 메모리에 올려야 하는데 녹화는 2GB 가 넘는다.
 *
 * 빌드: tools/build_mpeg4dec.sh
 */
#include <stdint.h>
#include <string.h>
#include <libavcodec/avcodec.h>
#include <libavutil/mem.h>
#include <emscripten/emscripten.h>

static AVCodecContext *ctx;
static AVPacket *pkt;
static AVFrame *frm;
static uint8_t *rgba;
static int rgba_cap;

EMSCRIPTEN_KEEPALIVE
int dec_open(const uint8_t *extra, int n) {
    const AVCodec *c = avcodec_find_decoder(AV_CODEC_ID_MPEG4);
    if (!c) return -1;
    if (ctx) avcodec_free_context(&ctx);
    ctx = avcodec_alloc_context3(c);
    if (!ctx) return -2;
    ctx->thread_count = 1;
    if (n > 0) {
        ctx->extradata = av_mallocz(n + AV_INPUT_BUFFER_PADDING_SIZE);
        memcpy(ctx->extradata, extra, n);
        ctx->extradata_size = n;
    }
    if (avcodec_open2(ctx, c, NULL) < 0) return -3;
    if (!pkt) pkt = av_packet_alloc();
    if (!frm) frm = av_frame_alloc();
    return 0;
}

/* 패킷 하나를 넣는다. n == 0 이면 「끝」 — 남은 그림을 비워 낸다.
 * pts 는 표시 순서 번호를 그대로 실어 보낸다(B 프레임이 있어도 맞는 그림을 고른다). */
EMSCRIPTEN_KEEPALIVE
int dec_send(const uint8_t *data, int n, double pts) {
    if (!ctx) return -1;
    if (n <= 0) return avcodec_send_packet(ctx, NULL);
    if (av_new_packet(pkt, n) < 0) return -2;
    memcpy(pkt->data, data, n);
    pkt->pts = (int64_t)pts;
    int r = avcodec_send_packet(ctx, pkt);
    av_packet_unref(pkt);
    return r;
}

/* 1 = 그림이 나왔다 · 0 = 더 넣어야 한다 · 음수 = 끝/오류 */
EMSCRIPTEN_KEEPALIVE
int dec_receive(void) {
    if (!ctx) return -1;
    int r = avcodec_receive_frame(ctx, frm);
    if (r == AVERROR(EAGAIN)) return 0;
    if (r < 0) return r;
    return 1;
}

EMSCRIPTEN_KEEPALIVE int dec_width(void) { return frm ? frm->width : 0; }
EMSCRIPTEN_KEEPALIVE int dec_height(void) { return frm ? frm->height : 0; }
EMSCRIPTEN_KEEPALIVE double dec_pts(void) {
    return frm ? (double)(frm->best_effort_timestamp != AV_NOPTS_VALUE
                          ? frm->best_effort_timestamp : frm->pts) : -1;
}

/* 탐색 전에 부른다 — 이전 GOP 의 참조 그림을 버린다 */
EMSCRIPTEN_KEEPALIVE
void dec_flush(void) { if (ctx) avcodec_flush_buffers(ctx); }

/* 색 변환표 — 화소마다 곱셈 다섯 번을 표 읽기로. 식은 아래 주석 그대로다
 * (1080p 한 장에 12ms → 표로 바꿔 그 절반 아래. 한 프레임 넘기기의 절반이 여기였다). */
static int tY[256], tRv[256], tGu[256], tGv[256], tBu[256];
static uint8_t tClip[1024];
static int tabled;

static void make_tables(void) {
    for (int i = 0; i < 256; i++) {
        tY[i] = (i - 16) * 298 + 128;
        tRv[i] = 409 * (i - 128);
        tGu[i] = -100 * (i - 128);
        tGv[i] = -208 * (i - 128);
        tBu[i] = 516 * (i - 128);
    }
    for (int i = 0; i < 1024; i++) {
        int v = i - 384;
        tClip[i] = v < 0 ? 0 : v > 255 ? 255 : v;
    }
    tabled = 1;
}

/* 방금 나온 그림을 RGBA 로. ★BT.601 제한 범위★ — mpeg4 의 기본이고
 * cv2(swscale) 도 이 식으로 BGR 을 만든다:
 *   R = (298(Y-16) + 409(V-128) + 128) >> 8
 *   G = (298(Y-16) - 100(U-128) - 208(V-128) + 128) >> 8
 *   B = (298(Y-16) + 516(U-128) + 128) >> 8
 * 색차는 최근접으로 늘린다(cv2 는 보간한다 — 차이는 경계에서 1~2 단계라
 * 보정에는 상관없다. 자체검사 t_mpeg4_js 가 그 폭을 잰다). */
EMSCRIPTEN_KEEPALIVE
uint8_t *dec_rgba(void) {
    if (!frm || frm->format != AV_PIX_FMT_YUV420P) return NULL;
    if (!tabled) make_tables();
    int w = frm->width, h = frm->height, need = w * h * 4;
    if (need > rgba_cap) {
        av_free(rgba);
        rgba = av_malloc(need);
        rgba_cap = rgba ? need : 0;
        if (!rgba) return NULL;
    }
    const uint8_t *C = tClip + 384;
    for (int y = 0; y < h; y++) {
        const uint8_t *Y = frm->data[0] + y * frm->linesize[0];
        const uint8_t *U = frm->data[1] + (y >> 1) * frm->linesize[1];
        const uint8_t *V = frm->data[2] + (y >> 1) * frm->linesize[2];
        uint32_t *o = (uint32_t *)(rgba + y * w * 4);
        for (int x = 0; x < w; x++) {
            int u = U[x >> 1], v = V[x >> 1], c = tY[Y[x]];
            int r = (c + tRv[v]) >> 8, g = (c + tGu[u] + tGv[v]) >> 8, b = (c + tBu[u]) >> 8;
            /* wasm 은 리틀엔디언 — 바이트 순서 R G B A */
            o[x] = (uint32_t)C[r] | ((uint32_t)C[g] << 8) | ((uint32_t)C[b] << 16) | 0xff000000u;
        }
    }
    return rgba;
}
