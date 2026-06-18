<script setup>
// Fullscreen viewer for email-channel messages. Pops the email out of the
// narrow conversation panel and renders it at a max-w-5xl viewport so wide
// tables / large signatures / image-heavy newsletters can actually be read
// without horizontal scrolling.
//
// The body is rendered through the SAME `Letter` component the in-place
// bubble uses, so the .letter-render styling (color-scheme: light, white
// background, slate-800 text) defined in Email/Index.vue applies here too
// — no duplicate CSS to maintain.
import { onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { OnClickOutside } from '@vueuse/components';
import { Letter } from 'vue-letter';
import { allowedCssProperties } from 'lettersanitizer';

import Icon from 'next/icon/Icon.vue';
import TeleportWithDirection from 'dashboard/components-next/TeleportWithDirection.vue';

const props = defineProps({
  show: { type: Boolean, default: false },
  subject: { type: String, default: '' },
  fromEmail: { type: String, default: '' },
  fromName: { type: String, default: '' },
  toEmails: { type: Array, default: () => [] },
  ccEmails: { type: Array, default: () => [] },
  bccEmails: { type: Array, default: () => [] },
  // The email body, fed straight to <Letter>. Should be the "full" HTML
  // (including quoted text) so the user gets the complete context here —
  // a fullscreen view is precisely the place you'd want to see everything.
  htmlContent: { type: String, default: '' },
  textContent: { type: String, default: '' },
});

const emit = defineEmits(['close']);

const close = () => emit('close');

// ESC key closes; lock body scroll while the modal is open so the chat
// timeline behind it doesn't jump when the user scrolls the modal.
const onKey = e => {
  if (e.key === 'Escape' && props.show) close();
};
const previousOverflow = ref('');
const applyBodyLock = locked => {
  if (typeof document === 'undefined') return;
  if (locked) {
    previousOverflow.value = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
  } else {
    document.body.style.overflow = previousOverflow.value;
  }
};
onMounted(() => window.addEventListener('keydown', onKey));
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey);
  applyBodyLock(false);
});
watch(() => props.show, value => applyBodyLock(value), { immediate: true });
</script>

<template>
  <TeleportWithDirection v-if="show" to="body">
    <div
      class="fixed inset-0 z-[9999] flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
    >
      <OnClickOutside @trigger="close">
        <div
          class="relative w-full max-w-5xl max-h-[90vh] overflow-hidden bg-n-background rounded-lg shadow-2xl flex flex-col"
        >
          <!-- Header: subject + from/to/cc/bcc + close -->
          <header
            class="shrink-0 px-6 py-4 border-b border-n-strong bg-n-background flex items-start justify-between gap-4"
          >
            <div class="flex-1 min-w-0">
              <h2
                v-if="subject"
                class="text-lg font-semibold text-n-slate-12 break-words"
              >
                {{ subject }}
              </h2>
              <div class="mt-2 text-sm text-n-slate-11 space-y-0.5 break-words">
                <div v-if="fromEmail" class="text-n-slate-12">
                  <template v-if="fromName">
                    <span class="font-medium">{{ fromName }}</span>
                    &lt;{{ fromEmail }}&gt;
                  </template>
                  <template v-else>
                    {{ fromEmail }}
                  </template>
                </div>
                <div v-if="toEmails.length">
                  <span class="text-n-slate-10">
                    {{ $t('EMAIL_HEADER.TO') }}:
                  </span>
                  {{ toEmails.join(', ') }}
                </div>
                <div v-if="ccEmails.length">
                  <span class="text-n-slate-10">
                    {{ $t('EMAIL_HEADER.CC') }}:
                  </span>
                  {{ ccEmails.join(', ') }}
                </div>
                <div v-if="bccEmails.length">
                  <span class="text-n-slate-10">
                    {{ $t('EMAIL_HEADER.BCC') }}:
                  </span>
                  {{ bccEmails.join(', ') }}
                </div>
              </div>
            </div>
            <button
              type="button"
              class="shrink-0 p-2 rounded-md hover:bg-n-slate-3 text-n-slate-11 hover:text-n-slate-12"
              :aria-label="$t('EMAIL_HEADER.CLOSE_FULLSCREEN')"
              @click="close"
            >
              <Icon icon="i-lucide-x" />
            </button>
          </header>

          <!-- Email body — scrolls independently when the email is long. -->
          <div class="flex-1 overflow-y-auto px-6 py-4">
            <Letter
              class-name="prose prose-bubble !max-w-none letter-render"
              :allowed-css-properties="[
                ...allowedCssProperties,
                'transform',
                'transform-origin',
              ]"
              :html="htmlContent"
              :text="textContent"
            />
          </div>
        </div>
      </OnClickOutside>
    </div>
  </TeleportWithDirection>
</template>
