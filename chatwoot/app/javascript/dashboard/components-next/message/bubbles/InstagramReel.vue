<script setup>
// Instagram Reel bubble.
//
// When a customer shares a reel in an Instagram DM, Meta's webhook delivers
// ONLY a permalink to the reel page (e.g. https://www.instagram.com/reel/XXX/)
// — never the underlying video file, and no real thumbnail. Chatwoot stores
// that permalink as the attachment's `dataUrl`.
//
// That means a plain <video :src="dataUrl"> can never play (the URL is an
// HTML page, not media) — which is exactly why reels rendered as a black
// player before this. Instead we render a clear, tappable card that opens
// the reel on Instagram in a new tab. Reliable for public AND private reels,
// no CSP/iframe dependency.
//
// (If true inline playback is ever wanted, Instagram's oEmbed iframe
// `${permalink}embed` can be embedded — but it only works for PUBLIC reels
// and needs instagram.com added to the dashboard CSP frame-src. Deliberately
// not done here to keep this robust on production.)

import { computed } from 'vue';
import BaseBubble from './Base.vue';
import Icon from 'next/icon/Icon.vue';
import FormattedContent from './Text/FormattedContent.vue';
import { useMessageContext } from '../provider.js';

const { attachments, content } = useMessageContext();

const attachment = computed(() => attachments.value?.[0] || {});
const reelUrl = computed(() => attachment.value?.dataUrl || '');
const hasCaption = computed(() => Boolean(content.value?.trim()));
</script>

<template>
  <BaseBubble class="overflow-hidden p-3" data-bubble-name="instagram-reel">
    <a
      :href="reelUrl"
      target="_blank"
      rel="noopener noreferrer"
      class="flex items-center gap-3 p-3 transition-colors rounded-lg bg-n-alpha-1 hover:bg-n-alpha-2 skip-context-menu"
    >
      <div
        class="flex items-center justify-center shrink-0 rounded-lg size-12 bg-gradient-to-tr from-amber-500 via-pink-500 to-purple-600"
      >
        <Icon icon="i-ri-instagram-line" class="text-white size-6" />
      </div>
      <div class="min-w-0">
        <p class="mb-0.5 font-medium text-n-slate-12">Instagram Reel</p>
        <p class="flex items-center gap-1 text-sm truncate text-n-slate-11">
          Open on Instagram
          <Icon icon="i-lucide-external-link" class="size-3.5" />
        </p>
      </div>
    </a>
    <div v-if="hasCaption" class="mt-2">
      <FormattedContent :content="content" />
    </div>
  </BaseBubble>
</template>
