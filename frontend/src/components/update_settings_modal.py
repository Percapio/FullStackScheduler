import re

with open("SettingsModal.vue", "r", encoding="utf-8") as f:
    text = f.read()

# Add auto copy API calls to imports
text = text.replace("} from '@/api/settings'", "  getAutoCopy,\n  saveAutoCopy,\n  runAutoCopyNow\n} from '@/api/settings'")

# Add state variables for auto-copy
state_vars = """
// Auto Copy State
const autoCopyLoaded = ref(false)
const autoCopyEnabled = ref(false)
const autoCopySource = ref('')
const autoCopyTime = ref('')
const autoCopySaving = ref(false)
const autoCopyInlineError = ref<string | null>(null)

// Auto Copy Status
const autoCopyRunning = ref(false)
const autoCopyLastOutcome = ref<string | null>(null)
const autoCopyLastFinished = ref<string | null>(null)

// Browser state
"""
text = text.replace("// Browser state\n", state_vars)

# Update fetchSettings to also fetch auto-copy
fetch_update = """  if (res.kind === 'ok') {
    editable.value = res.editable
    configured.value = res.configured
    source.value = res.source
    serverPath.value = res.path
    
    if (res.editable) {
      fieldPath.value = res.path ?? ''
      await navigateBrowse(res.path ?? '')
      
      // Fetch Auto Copy
      const acRes = await getAutoCopy()
      if (acRes.kind === 'ok') {
        autoCopyLoaded.value = true
        autoCopyEnabled.value = acRes.data.enabled
        autoCopySource.value = acRes.data.source ?? ''
        autoCopyTime.value = acRes.data.scheduled_time ?? ''
        autoCopyRunning.value = acRes.data.running
        autoCopyLastOutcome.value = acRes.data.last_run_outcome
        autoCopyLastFinished.value = acRes.data.last_run_finished_at
      }
    }
  } else if (res.kind === 'forbidden') {"""
text = text.replace("""  if (res.kind === 'ok') {
    editable.value = res.editable
    configured.value = res.configured
    source.value = res.source
    serverPath.value = res.path
    
    if (res.editable) {
      fieldPath.value = res.path ?? ''
      await navigateBrowse(res.path ?? '')
    }
  } else if (res.kind === 'forbidden') {""", fetch_update)

# Add saveAutoCopy logic
save_ac_fn = """const isDirty = computed(() => {
  const s = serverPath.value ?? ''
  const f = fieldPath.value ?? ''
  return s !== f
})

async function onSaveAutoCopy() {
  if (autoCopySaving.value) return
  
  autoCopySaving.value = true
  autoCopyInlineError.value = null
  
  const res = await saveAutoCopy(autoCopyEnabled.value, autoCopySource.value, autoCopyTime.value)
  autoCopySaving.value = false
  
  if (res.kind === 'ok') {
    pushToast('Auto-copy settings saved.', 'success')
  } else if (res.kind === 'invalid') {
    autoCopyInlineError.value = res.reason
  } else {
    pushToast('Failed to save auto-copy settings.', 'error')
  }
}

async function onRunAutoCopy() {
  const res = await runAutoCopyNow()
  if (res.kind === 'ok') {
    pushToast('Sync started in background.', 'success')
    autoCopyRunning.value = true
  } else if (res.kind === 'conflict') {
    pushToast('Sync is already running or disabled.', 'error')
  } else {
    pushToast('Failed to start sync.', 'error')
  }
}
"""
text = text.replace("const isDirty = computed(() => {\n  const s = serverPath.value ?? ''\n  const f = fieldPath.value ?? ''\n  return s !== f\n})", save_ac_fn)

# Add Auto Copy UI
ui_update = """            </section>
            
            <section v-if="autoCopyLoaded" class="space-y-4 pt-6 border-t border-slate-200 dark:border-slate-700">
              <div class="flex items-center justify-between">
                <h3 class="text-sm font-medium text-slate-800 dark:text-slate-100">Unattended Sync</h3>
                <label class="flex items-center gap-2 text-sm cursor-pointer">
                  <input type="checkbox" v-model="autoCopyEnabled" class="rounded border-slate-300 text-sky-600 focus:ring-sky-500" />
                  <span class="text-slate-700 dark:text-slate-200">Enable</span>
                </label>
              </div>
              
              <div class="space-y-3" :class="{'opacity-50 pointer-events-none': !autoCopyEnabled}">
                <div>
                  <label class="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">Source Share</label>
                  <input v-model="autoCopySource" type="text" 
                         class="w-full rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-3 py-1.5 text-sm focus-ring"
                         placeholder="e.g. \\\\SERVER\\Photos" />
                </div>
                <div>
                  <label class="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">Schedule Time (HH:MM)</label>
                  <input v-model="autoCopyTime" type="time" 
                         class="w-full rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-3 py-1.5 text-sm focus-ring" />
                </div>
              </div>
              
              <div class="flex items-center justify-between pt-2">
                <div class="flex gap-2">
                  <button type="button" @click="onSaveAutoCopy" :disabled="autoCopySaving"
                          class="px-4 py-1.5 rounded bg-slate-200 dark:bg-slate-700 text-slate-800 dark:text-slate-100 text-sm font-medium hover:bg-slate-300 dark:hover:bg-slate-600 disabled:opacity-50 focus-ring">
                    Save Sync Settings
                  </button>
                  <button type="button" @click="onRunAutoCopy" :disabled="!autoCopyEnabled || autoCopyRunning"
                          class="px-4 py-1.5 rounded bg-sky-600 text-white text-sm font-medium hover:bg-accent-700 disabled:opacity-50 focus-ring">
                    Sync Now
                  </button>
                </div>
                <div class="text-xs text-slate-500 text-right">
                  <div v-if="autoCopyRunning" class="text-sky-600 font-medium animate-pulse">Sync is running...</div>
                  <div v-else-if="autoCopyLastOutcome">Last run: {{ autoCopyLastOutcome }} ({{ autoCopyLastFinished ? new Date(autoCopyLastFinished).toLocaleString() : 'unknown time' }})</div>
                  <div v-else>Never run</div>
                </div>
              </div>
              <p v-if="autoCopyInlineError" class="text-sm text-warn-600">{{ autoCopyInlineError }}</p>
            </section>
          </div>
        </div>
      </div>"""

text = text.replace("            </section>\n          </div>\n        </div>\n      </div>", ui_update)


with open("SettingsModal.vue", "w", encoding="utf-8") as f:
    f.write(text)
