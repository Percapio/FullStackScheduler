import re

with open("frontend/src/components/PhotoGalleryModal.vue", "r") as f:
    content = f.read()

# 1. Generation watcher
content = content.replace(
    """watch(() => (state.value.state === 'ready' ? state.value.date_folder : null), (newFolder, oldFolder) => {""",
    """watch(() => (state.value.state === 'ready' ? `${state.value.date_folder}/${state.value.sub_folder}` : null), (newFolder, oldFolder) => {"""
)

# 2. thumbUrl
content = content.replace(
    """function thumbUrl(filename: string, date_folder: string, version: string) {
  return `/api/photos/thumb/${encodeURIComponent(filename)}?date_folder=${encodeURIComponent(date_folder)}&v=${encodeURIComponent(version)}`
}""",
    """function thumbUrl(filename: string, date_folder: string, sub_folder: string, version: string) {
  return `/api/photos/thumb/${encodeURIComponent(filename)}?date_folder=${encodeURIComponent(date_folder)}&sub_folder=${encodeURIComponent(sub_folder)}&v=${encodeURIComponent(version)}`
}"""
)

# 3. Breadcrumbs
content = content.replace(
    """                  <span>Photos for {{ state.date_folder }}</span>""",
    """                  <div class="flex items-center gap-2">
                    <button v-if="state.sub_folder !== ''" @click="props.gallery.navigateUp()" class="text-sm px-2 py-1 bg-slate-100 hover:bg-slate-200 dark:bg-slate-700 dark:hover:bg-slate-600 rounded">
                      &larr; Back
                    </button>
                    <span>
                      <template v-if="state.sub_folder === ''">Photos for {{ state.date_folder }}</template>
                      <template v-else>
                        <a href="#" @click.prevent="props.gallery.navigateUp()" class="hover:underline">{{ state.date_folder }}</a> / {{ state.sub_folder }}
                      </template>
                    </span>
                  </div>"""
)

# 4. Truncation
content = content.replace(
    """              <div v-if="state.truncated" class="mb-4 p-3 bg-amber-50 border border-amber-200 text-amber-800 rounded text-sm">
                This folder contains more photos than can be displayed. Showing the first {{ state.entries.length }}.
              </div>""",
    """              <div v-if="state.folders_truncated" class="mb-4 p-3 bg-amber-50 border border-amber-200 text-amber-800 rounded text-sm">
                This folder contains more job folders than can be shown. Showing the first {{ state.folders.length }}.
              </div>
              <div v-if="state.truncated" class="mb-4 p-3 bg-amber-50 border border-amber-200 text-amber-800 rounded text-sm">
                This folder contains more photos than can be displayed. Showing the first {{ state.entries.length }}.
              </div>"""
)

# 5. Empty state and Folders array
content = content.replace(
    """                <div v-if="state.entries.length === 0" class="text-center py-12 text-slate-500">
                  No previewable photos found in this folder.
                </div>""",
    """                <div v-if="state.entries.length === 0 && state.folders.length === 0" class="text-center py-12 text-slate-500">
                  No previewable photos found in this folder.
                </div>
                
                <div v-if="state.folders.length > 0" class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4 mb-6">
                  <div
                    v-for="folder in state.folders"
                    :key="`${state.date_folder}/${folder}`"
                    class="relative aspect-[4/3] cursor-pointer group rounded overflow-hidden bg-slate-100 hover:bg-slate-200 dark:bg-slate-700 dark:hover:bg-slate-600 flex flex-col items-center justify-center border border-slate-200 dark:border-slate-600 transition-colors"
                    @click="props.gallery.navigateTo(folder)"
                  >
                    <svg class="w-10 h-10 text-slate-400 mb-2 group-hover:text-blue-500 transition-colors" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
                    </svg>
                    <span class="text-sm font-medium text-slate-700 dark:text-slate-300 px-2 truncate w-full text-center" :title="folder">{{ folder }}</span>
                  </div>
                </div>"""
)

# 6. thumbUrl invocation and keys
content = content.replace(
    """:key="`${state.date_folder}/${entry.name}/${retryNonces[entry.name] || 0}`\"""",
    """:key="`${state.date_folder}/${state.sub_folder}/${entry.name}/${retryNonces[entry.name] || 0}`\""""
)

content = content.replace(
    """:src="thumbUrl(entry.name, state.date_folder, entry.version)\"""",
    """:src="thumbUrl(entry.name, state.date_folder, state.sub_folder, entry.version)\""""
)


with open("frontend/src/components/PhotoGalleryModal.vue", "w") as f:
    f.write(content)
