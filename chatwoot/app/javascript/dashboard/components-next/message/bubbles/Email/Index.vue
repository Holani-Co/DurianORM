<script setup>
import { computed, useTemplateRef, ref, onMounted } from 'vue';
import { Letter } from 'vue-letter';
import { sanitizeTextForRender } from '@chatwoot/utils';
import { allowedCssProperties } from 'lettersanitizer';

import Icon from 'next/icon/Icon.vue';
import { EmailQuoteExtractor } from 'dashboard/helper/emailQuoteExtractor.js';
import FormattedContent from 'next/message/bubbles/Text/FormattedContent.vue';
import BaseBubble from 'next/message/bubbles/Base.vue';
import AttachmentChips from 'next/message/chips/AttachmentChips.vue';
import EmailMeta from './EmailMeta.vue';
import EmailFullscreenModal from './EmailFullscreenModal.vue';
import TranslationToggle from 'dashboard/components-next/message/TranslationToggle.vue';

import { useMessageContext } from '../../provider.js';
import { MESSAGE_TYPES } from 'next/message/constants.js';
import { useTranslations } from 'dashboard/composables/useTranslations';

const { content, contentAttributes, attachments, messageType, sender } =
  useMessageContext();

const isExpandable = ref(false);
const isExpanded = ref(false);
const showQuotedMessage = ref(false);
const renderOriginal = ref(false);
const showFullscreen = ref(false);
const contentContainer = useTemplateRef('contentContainer');

onMounted(() => {
  isExpandable.value = contentContainer.value?.scrollHeight > 400;
});

const isOutgoing = computed(() => messageType.value === MESSAGE_TYPES.OUTGOING);
const isIncoming = computed(() => !isOutgoing.value);

const { hasTranslations, translationContent } =
  useTranslations(contentAttributes);

const originalEmailText = computed(() => {
  const text =
    contentAttributes?.value?.email?.textContent?.full ?? content.value;
  return sanitizeTextForRender(text);
});

const originalEmailHtml = computed(
  () =>
    contentAttributes?.value?.email?.htmlContent?.full ||
    originalEmailText.value
);

const hasEmailContent = computed(() => {
  return (
    contentAttributes?.value?.email?.textContent?.full ||
    contentAttributes?.value?.email?.htmlContent?.full
  );
});

const messageContent = computed(() => {
  // If translations exist and we're showing translations (not original)
  if (hasTranslations.value && !renderOriginal.value) {
    return translationContent.value;
  }
  // Otherwise show original content
  return content.value;
});

const textToShow = computed(() => {
  // If translations exist and we're showing translations (not original)
  if (hasTranslations.value && !renderOriginal.value) {
    return translationContent.value;
  }
  // Otherwise show original text
  return originalEmailText.value;
});

const fullHTML = computed(() => {
  // If translations exist and we're showing translations (not original)
  if (hasTranslations.value && !renderOriginal.value) {
    return translationContent.value;
  }
  // Otherwise show original HTML
  return originalEmailHtml.value;
});

const unquotedHTML = computed(() =>
  EmailQuoteExtractor.extractQuotes(fullHTML.value)
);

const hasQuotedMessage = computed(() =>
  EmailQuoteExtractor.hasQuotes(fullHTML.value)
);

// Ensure unique keys for <Letter> when toggling between original and translated views.
// This forces Vue to re-render the component and update content correctly.
const translationKeySuffix = computed(() => {
  if (renderOriginal.value) return 'original';
  if (hasTranslations.value) return 'translated';
  return 'original';
});

const handleSeeOriginal = () => {
  renderOriginal.value = !renderOriginal.value;
};

// Mirror EmailMeta's address resolution here so the fullscreen modal can be
// fed the same fields without duplicating it through props from the parent.
const emailMeta = computed(() => contentAttributes?.value?.email || {});
const subjectForModal = computed(() => emailMeta.value.subject || '');
const fromEmailForModal = computed(() => (emailMeta.value.from || [])[0] || '');
const fromNameForModal = computed(() => {
  // Same rule as EmailMeta: only show the conversation sender's display
  // name when the per-message "from" matches that sender's email.
  const senderEmail = sender.value?.email || '';
  if (fromEmailForModal.value && fromEmailForModal.value === senderEmail) {
    return sender.value?.name || '';
  }
  return '';
});
const toEmailsForModal = computed(
  () => emailMeta.value.to || contentAttributes?.value?.toEmails || []
);
const ccEmailsForModal = computed(
  () => contentAttributes?.value?.ccEmails || emailMeta.value.cc || []
);
const bccEmailsForModal = computed(
  () => contentAttributes?.value?.bccEmails || emailMeta.value.bcc || []
);
</script>

<template>
  <BaseBubble
    class="w-full relative"
    :class="{
      'bg-white': isIncoming,
      'bg-blue-50': isOutgoing,
    }"
    data-bubble-name="email"
  >
    <!-- Pop-out button: opens the email in a fullscreen modal so wide
         emails (newsletters, marketing templates, long signatures) can
         be read without horizontal scrolling inside the narrow
         conversation panel. -->
    <button
      v-if="hasEmailContent || messageContent"
      type="button"
      class="absolute top-2 right-2 z-10 p-1.5 rounded-md text-n-slate-11 hover:text-n-slate-12 hover:bg-n-alpha-2 transition-colors"
      :aria-label="$t('EMAIL_HEADER.OPEN_FULLSCREEN')"
      :title="$t('EMAIL_HEADER.OPEN_FULLSCREEN')"
      @click="showFullscreen = true"
    >
      <Icon icon="i-lucide-maximize-2" class="text-base" />
    </button>
    <EmailMeta
      class="p-3"
      :class="{
        'border-b border-n-strong': isIncoming,
        'border-b border-n-slate-8/20': isOutgoing,
      }"
    />
    <section ref="contentContainer" class="p-3">
      <div
        :class="{
          'max-h-[400px] overflow-hidden relative': !isExpanded && isExpandable,
          'overflow-y-scroll relative': isExpanded,
        }"
      >
        <div
          v-if="isExpandable && !isExpanded"
          class="absolute left-0 right-0 bottom-0 h-40 px-8 flex items-end"
          :class="{
            'bg-gradient-to-t from-n-slate-4 via-n-slate-4 via-20% to-transparent':
              isIncoming,
            'bg-gradient-to-t from-n-solid-blue via-n-solid-blue via-20% to-transparent':
              isOutgoing,
          }"
        >
          <button
            class="text-n-slate-12 py-2 px-8 mx-auto text-center flex items-center gap-2"
            @click="isExpanded = true"
          >
            <Icon icon="i-lucide-maximize-2" />
            {{ $t('EMAIL_HEADER.EXPAND') }}
          </button>
        </div>
        <FormattedContent
          v-if="isOutgoing && content && !hasEmailContent"
          class="text-n-slate-12"
          :content="messageContent"
        />
        <template v-else>
          <Letter
            v-if="showQuotedMessage"
            :key="`letter-quoted-${translationKeySuffix}`"
            class-name="prose prose-bubble !max-w-none letter-render"
            :allowed-css-properties="[
              ...allowedCssProperties,
              'transform',
              'transform-origin',
            ]"
            :html="fullHTML"
            :text="textToShow"
          />
          <Letter
            v-else
            :key="`letter-unquoted-${translationKeySuffix}`"
            class-name="prose prose-bubble !max-w-none letter-render"
            :html="unquotedHTML"
            :allowed-css-properties="[
              ...allowedCssProperties,
              'transform',
              'transform-origin',
            ]"
            :text="textToShow"
          />
        </template>
        <button
          v-if="hasQuotedMessage"
          class="text-n-slate-11 px-1 leading-none text-sm bg-n-alpha-black2 text-center flex items-center gap-1 mt-2"
          @click="showQuotedMessage = !showQuotedMessage"
        >
          <template v-if="showQuotedMessage">
            {{ $t('CHAT_LIST.HIDE_QUOTED_TEXT') }}
          </template>
          <template v-else>
            {{ $t('CHAT_LIST.SHOW_QUOTED_TEXT') }}
          </template>
          <Icon
            :icon="
              showQuotedMessage
                ? 'i-lucide-chevron-up'
                : 'i-lucide-chevron-down'
            "
          />
        </button>
      </div>
    </section>
    <TranslationToggle
      v-if="hasTranslations"
      class="py-2 px-3"
      :showing-original="renderOriginal"
      @toggle="handleSeeOriginal"
    />
    <section
      v-if="Array.isArray(attachments) && attachments.length"
      class="px-4 pb-4 space-y-2"
    >
      <AttachmentChips :attachments="attachments" class="gap-1" />
    </section>
    <EmailFullscreenModal
      :show="showFullscreen"
      :subject="subjectForModal"
      :from-email="fromEmailForModal"
      :from-name="fromNameForModal"
      :to-emails="toEmailsForModal"
      :cc-emails="ccEmailsForModal"
      :bcc-emails="bccEmailsForModal"
      :html-content="fullHTML"
      :text-content="textToShow"
      @close="showFullscreen = false"
    />
  </BaseBubble>
</template>

<style lang="scss">
// Tailwind resets break the rendering of google drive link in Gmail messages
// This fixes it using https://developer.mozilla.org/en-US/docs/Web/CSS/Attribute_selectors

.letter-render [class*='gmail_drive_chip'] {
  box-sizing: initial;
  @apply bg-n-slate-4 border-n-slate-6 rounded-md !important;

  a {
    @apply text-n-slate-12 !important;

    img {
      display: inline-block;
    }
  }
}

// Email clients (Gmail, Outlook) hardcode dir="ltr" on wrapper elements.
// In RTL apps this forces email content LTR regardless of actual text.
[dir='rtl'] .letter-render [dir='ltr'] {
  direction: inherit;
}

// Render HTML email content with its intended light-mode contrast.
//
// Why: emails (Gmail security alerts, newsletters, password-reset notices,
// etc.) are universally authored against a white canvas. In Chatwoot's dark
// theme the email's own dark-text styles collapse to dark-on-dark and become
// unreadable (e.g. screenshot in PR description).
//
// How:
//   * `color-scheme: light` tells the browser to render this subtree's UA
//     defaults (form controls, scrollbar, link visited state, etc.) as if
//     it were a light theme — the W3C-recommended way to opt one region
//     out of dark mode. Ref: https://web.dev/articles/color-scheme
//   * The explicit bg + text colour pair give unstyled spans a known
//     contrast — `color-scheme` alone doesn't paint the background.
//   * `:where(...)` keeps the link colour at specificity 0, so any email
//     that sets its own link colour (inline or via class) still wins.
//   * Theme tokens (`@apply rounded-md`) keep design consistency where
//     possible, but the bg/text colours are intentionally raw hex — they
//     must stay light regardless of the app's active theme.
//
// Considered alternatives:
//   * Rendering inside a sandboxed <iframe srcdoc=...>: perfect isolation
//     but complicates resize, click-through, in-content search, and the
//     vue-letter sanitiser already neuters scripts/styles for us.
//   * Theme-aware background (e.g. `n-slate-1` in dark, transparent in
//     light): emails would still render in the app's theme — same bug,
//     just less severe in dark mode.
.letter-render {
  color-scheme: light;
  background-color: #ffffff;
  color: #1f2937; // slate-800 — readable default for unstyled text
  @apply rounded-md;
  padding: 0.75rem 0.875rem;

  // Default blue for links the email didn't style itself.
  a:where(:not([style*='color'])) {
    color: #2563eb; // blue-600
  }

  img {
    max-width: 100%;
    height: auto;
  }

  // Force minimum contrast on body text. Senders routinely author
  // signatures / footers / addresses in #ccc-ish grey, which becomes
  // unreadable against our white email canvas (see screenshots: the
  // Durian "Neha Singh / Customer Support / Goregaon…" block and the
  // Zoom "© 2026 Zoom" footer both ghost out). !important is needed
  // because the dim colour is set as an INLINE style attribute on
  // each element, which beats normal selector specificity.
  //
  // Excluded by design: headings (h1-h6) and emphasis (b/strong/em/i)
  // — those are where senders typically apply intentional brand
  // colour, and they're usually short enough that a coloured shade
  // remains readable even when subtle. Anchors are excluded so link
  // styling continues to follow the sender or our fallback above.
  p, div, span, td, th, li, blockquote, font, address {
    color: #1f2937 !important;
  }
}

// ── Whole email box in a fixed LIGHT theme (Gmail-style reading pane) ──
// The email BODY already forces light via .letter-render (external email HTML
// is authored for a white background). This extends the same to the rest of
// the email box — the meta header (from/to/subject/date), our own outgoing
// reply text, and the collapse fade — so the ENTIRE email box stays readable
// regardless of the app's dark theme. The bubble background is set to a light
// colour on the BaseBubble above.
[data-bubble-name='email'] {
  color-scheme: light;

  // Meta header + inline controls use theme tokens that render light-on-dark;
  // pin them dark so they read on the light bubble.
  :is(.text-n-slate-11, .text-n-slate-12) {
    color: #475569 !important; // slate-600
  }
  :is(.border-n-strong, [class*='border-n-slate-8']) {
    border-color: #e5e7eb !important; // slate-200
  }

  // Our own outgoing reply text (rendered via prose, not .letter-render).
  .prose {
    color: #1f2937;
  }

  // The "read more" collapse fade references dark theme tokens — keep it light
  // so it blends with the white/blue email canvas instead of a dark band.
  .from-n-slate-4 {
    --tw-gradient-from: #ffffff var(--tw-gradient-from-position);
  }
  .from-n-solid-blue {
    --tw-gradient-from: #eff6ff var(--tw-gradient-from-position);
  }
  .via-n-slate-4 {
    --tw-gradient-to: rgb(255 255 255 / 0);
    --tw-gradient-stops: var(--tw-gradient-from), #ffffff var(--tw-gradient-via-position), var(--tw-gradient-to);
  }
  .via-n-solid-blue {
    --tw-gradient-to: rgb(239 246 255 / 0);
    --tw-gradient-stops: var(--tw-gradient-from), #eff6ff var(--tw-gradient-via-position), var(--tw-gradient-to);
  }
}
</style>
