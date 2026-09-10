import type {
  FrontendExtension,
  FrontendExtensionLoadError,
  FrontendExtensionModule,
  FrontendExtensionNavigation,
  FrontendExtensionRoute,
  FrontendSlotName,
  FrontendSlotRegistration,
} from './types'
import { FRONTEND_EXTENSION_API_VERSION } from './types'

const ID_RE = /^[a-z0-9]+(?:[._-][a-z0-9]+)*$/

const extensions = new Map<string, FrontendExtension>()
const routes = new Map<string, FrontendExtensionRoute & { extensionId: string }>()
const navigation = new Map<string, FrontendExtensionNavigation & { extensionId: string }>()
const slots = new Map<FrontendSlotName, Array<FrontendSlotRegistration & { extensionId: string }>>()
const loadErrors: FrontendExtensionLoadError[] = []
let frozen = false

function assertMutable() {
  if (frozen) throw new Error('前端擴展註冊表已凍結')
}

function assertId(value: string, label: string) {
  if (!ID_RE.test(value)) throw new Error(`${label} 非法: ${value}`)
}

function registerExtension(extension: FrontendExtension) {
  assertMutable()
  assertId(extension.id, '擴展 ID')
  if (extension.apiVersion !== FRONTEND_EXTENSION_API_VERSION) {
    throw new Error(
      `擴展 ${extension.id} 需要前端契約 v${extension.apiVersion}, 當前為 v${FRONTEND_EXTENSION_API_VERSION}`,
    )
  }
  if (extensions.has(extension.id)) throw new Error(`擴展 ID 重複: ${extension.id}`)

  const localRouteIds = new Set<string>()
  const localRoutePaths = new Set<string>()
  for (const route of extension.routes ?? []) {
    assertId(route.id, '路由 ID')
    if (!route.path.startsWith('/') || route.path === '/') {
      throw new Error(`擴展路由必須使用非根絕對路徑: ${route.path}`)
    }
    if (routes.has(route.id) || localRouteIds.has(route.id)) {
      throw new Error(`擴展路由 ID 重複: ${route.id}`)
    }
    const duplicatePath = [...routes.values()].find(item => item.path === route.path)
    if (duplicatePath || localRoutePaths.has(route.path)) {
      throw new Error(`擴展路由路徑重複: ${route.path}`)
    }
    localRouteIds.add(route.id)
    localRoutePaths.add(route.path)
  }

  const localNavigationIds = new Set<string>()
  for (const item of extension.navigation ?? []) {
    assertId(item.id, '導航 ID')
    if (navigation.has(item.id) || localNavigationIds.has(item.id)) {
      throw new Error(`擴展導航 ID 重複: ${item.id}`)
    }
    localNavigationIds.add(item.id)
  }

  const localSlotIds = new Set<string>()
  for (const slot of extension.slots ?? []) {
    assertId(slot.id, '插槽實現 ID')
    const key = `${slot.name}:${slot.id}`
    if (localSlotIds.has(key)) throw new Error(`擴展內插槽實現重複: ${key}`)
    if ((slots.get(slot.name) ?? []).some(item => item.id === slot.id)) {
      throw new Error(`插槽實現 ID 重複: ${key}`)
    }
    localSlotIds.add(key)
  }

  extensions.set(extension.id, extension)
  for (const route of extension.routes ?? []) {
    routes.set(route.id, { ...route, extensionId: extension.id })
  }
  for (const item of extension.navigation ?? []) {
    navigation.set(item.id, { ...item, extensionId: extension.id })
  }
  for (const slot of extension.slots ?? []) {
    const items = slots.get(slot.name) ?? []
    items.push({ ...slot, extensionId: extension.id })
    slots.set(slot.name, items)
  }
}

export async function loadFrontendExtensions(
  modules: Record<string, () => Promise<FrontendExtensionModule>>,
) {
  for (const source of Object.keys(modules).sort()) {
    let extension: FrontendExtension | undefined
    try {
      extension = (await modules[source]()).default
      if (!extension) throw new Error('模塊必須 default export FrontendExtension')
      registerExtension(extension)
    } catch (error) {
      loadErrors.push({
        source,
        extensionId: extension?.id,
        error: error instanceof Error ? error.message : String(error),
      })
    }
  }
}

export function finalizeFrontendExtensions(reservedPaths: ReadonlySet<string>) {
  if (frozen) return
  for (const extension of [...extensions.values()]) {
    try {
      for (const route of extension.routes ?? []) {
        if (route.path.includes(':') || route.path.includes('*')) {
          throw new Error(`擴展路由首版只支持靜態路徑: ${route.path}`)
        }
        if ([...reservedPaths].some(corePath => coreRouteMatches(corePath, route.path))) {
          throw new Error(`試圖覆蓋核心路由 ${route.path}`)
        }
      }
      const ownRouteIds = new Set((extension.routes ?? []).map(route => route.id))
      for (const item of extension.navigation ?? []) {
        if (!ownRouteIds.has(item.routeId)) {
          throw new Error(`導航 ${item.id} 引用了本擴展中不存在的路由 ${item.routeId}`)
        }
      }
    } catch (error) {
      loadErrors.push({
        source: extension.id,
        extensionId: extension.id,
        error: error instanceof Error ? error.message : String(error),
      })
      removeExtension(extension.id)
    }
  }
  for (const items of slots.values()) {
    items.sort((a, b) => (a.order ?? 100) - (b.order ?? 100) || a.id.localeCompare(b.id))
  }
  frozen = true
}

function removeExtension(extensionId: string) {
  extensions.delete(extensionId)
  for (const [id, route] of routes) {
    if (route.extensionId === extensionId) routes.delete(id)
  }
  for (const [id, item] of navigation) {
    if (item.extensionId === extensionId) navigation.delete(id)
  }
  for (const [name, items] of slots) {
    slots.set(name, items.filter(item => item.extensionId !== extensionId))
  }
}

function coreRouteMatches(pattern: string, path: string) {
  const patternParts = pattern.split('/').filter(Boolean)
  const pathParts = path.split('/').filter(Boolean)
  return patternParts.length === pathParts.length
    && patternParts.every((part, index) => part.startsWith(':') || part === pathParts[index])
}

export function getFrontendExtensionRoutes() {
  if (!frozen) throw new Error('讀取擴展路由前必須凍結註冊表')
  return [...routes.values()].sort((a, b) => a.path.localeCompare(b.path))
}

export function getFrontendExtensionNavigation() {
  if (!frozen) throw new Error('讀取擴展導航前必須凍結註冊表')
  return [...navigation.values()]
    .map(item => ({ ...item, route: routes.get(item.routeId)! }))
    .sort((a, b) => (a.order ?? 100) - (b.order ?? 100) || a.id.localeCompare(b.id))
}

export function getFrontendSlotRegistrations<K extends FrontendSlotName>(name: K) {
  if (!frozen) throw new Error('讀取擴展插槽前必須凍結註冊表')
  // 存儲按槽位名分桶, 桶內註冊項的 name 必與鍵一致, 斷言安全
  return (slots.get(name) ?? []) as unknown as Array<FrontendSlotRegistration<K> & { extensionId: string }>
}

export function getFrontendExtensionLoadErrors() {
  return [...loadErrors]
}
