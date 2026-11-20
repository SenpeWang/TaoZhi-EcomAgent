<template>
  <LoginPortal
    v-if="!user"
    v-model:username="loginForm.username"
    v-model:password="loginForm.password"
    :info="workspaceInfo"
    :busy="busy"
    @sign-in="signIn"
    @pick-account="pickAccount"
  />

  <div v-else class="app-shell">
    <aside class="sidebar">
      <div class="sidebar-brand">
        <span class="brand-monogram" aria-hidden="true">淘</span>
        <strong class="brand-title">淘智企业工作台</strong>
      </div>

      <div class="tenant-badge-box">
        <span class="tenant-name-text" :title="workspaceInfo.name">
          {{ workspaceInfo.name || '企业工作空间' }}
        </span>
        <span class="tenant-code">
          {{ workspaceInfo.tenant_code || 'default' }}
        </span>
      </div>

      <nav class="sidebar-nav" aria-label="主导航">
        <template v-for="group in navigationGroups" :key="group.title">
          <div class="nav-group-label">{{ group.title }}</div>
          <button
            v-for="item in group.items"
            :key="item.page"
            type="button"
            class="nav-item-btn"
            :class="{ active: currentPage === item.page }"
            :aria-current="currentPage === item.page ? 'page' : undefined"
            @click="navigate(item.page)"
          >
            <svg
              class="nav-svg-icon"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <path v-for="path in workspacePages[item.page].iconPaths" :key="path" :d="path" />
            </svg>
            <span class="nav-label">{{ item.page }}</span>
            <span v-if="item.badge" class="nav-badge-count">{{ item.badge }}</span>
          </button>
        </template>
      </nav>

      <div class="sidebar-footer">
        <div class="sidebar-security-chip">
          <svg
            class="nav-svg-icon"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.8"
          >
            <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
          </svg>
          <span>数据权限：{{ clearanceShortText }}</span>
        </div>
      </div>
    </aside>

    <div class="main-shell">
      <header class="topbar">
        <div class="topbar-breadcrumb">
          <span class="bc-platform">{{ workspaceInfo.name || '企业工作台' }}</span>
          <span class="bc-slash">/</span>
          <span class="bc-current">{{
            user.must_change ? '账号安全' : currentPageDefinition.group
          }}</span>
        </div>

        <select
          class="mobile-navigation"
          aria-label="工作台导航"
          :value="currentPage"
          @change="navigate(($event.target as HTMLSelectElement).value)"
        >
          <optgroup v-for="group in navigationGroups" :key="group.title" :label="group.title">
            <option v-for="item in group.items" :key="item.page" :value="item.page">
              {{ item.page }}
            </option>
          </optgroup>
        </select>
        <div class="topbar-right">
          <button
            v-if="capabilities.permissions && pendingApprovalCount > 0"
            type="button"
            class="topbar-alert-btn"
            @click="navigate('权限审批')"
          >
            <span>待审批 {{ pendingApprovalCount }}</span>
          </button>

          <el-dropdown trigger="click" @command="handleUserCommand">
            <button type="button" class="user-dropdown-trigger">
              <span class="topbar-avatar" :class="userToneClass">
                {{ userInitial }}
              </span>
              <span class="user-name-col">
                <strong class="user-realname">{{ user.display_name }}</strong>
                <small class="user-roles-text">{{ displayRoles(user) }}</small>
              </span>
              <svg
                class="chevron-svg"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
              >
                <polyline points="6 9 12 15 18 9" />
              </svg>
            </button>
            <template #dropdown>
              <el-dropdown-menu>
                <div class="dropdown-user-header">
                  <strong>{{ user.display_name }}</strong>
                  <small>账号：{{ user.username }} · {{ primaryOrgName }}</small>
                  <span class="dropdown-clearance-tag">数据权限：{{ clearanceText }}</span>
                </div>
                <el-dropdown-item command="account">个人中心</el-dropdown-item>
                <el-dropdown-item command="tasks">我的任务</el-dropdown-item>
                <el-dropdown-item divided command="signout">退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </header>

      <main class="content">
        <div class="page-heading">
          <div>
            <h1>{{ currentPage }}</h1>
            <p>{{ currentPageDefinition.description }}</p>
          </div>
        </div>

        <el-alert
          v-if="user.must_change"
          type="warning"
          show-icon
          :closable="false"
          title="安全提醒：当前账号使用初始密码或重置后的临时密码登录，请先在个人中心修改登录密码。"
        />

        <template v-if="currentPage === '工作概览'">
          <section class="overview-banner">
            <div class="overview-banner-meta">
              <h2>{{ primaryOrgName }}</h2>
              <p>
                {{ user.display_name }} · {{ displayRoles(user) }} · 数据权限 {{ clearanceText }}
              </p>
            </div>
            <div class="overview-banner-actions">
              <el-button v-if="user.system_admin" @click="navigate('轨迹中台')"
                >查看执行轨迹</el-button
              >
              <el-button @click="navigate('知识问答')">发起知识问答</el-button>
              <el-button type="primary" @click="navigate('选品调研')">开始选品调研</el-button>
            </div>
          </section>

          <section class="stat-grid">
            <div class="stat-card">
              <div class="stat-card-top">
                <span>可访问文档</span>
              </div>
              <strong>{{ dashboard.documents }}<small>份</small></strong>
              <span class="stat-foot">当前权限范围内文档</span>
            </div>
            <div class="stat-card">
              <div class="stat-card-top">
                <span>分析任务</span>
              </div>
              <strong>{{ dashboard.tasks }}<small>项</small></strong>
              <span class="stat-foot">知识检索与选品调研</span>
            </div>
            <div class="stat-card">
              <div class="stat-card-top">
                <span>待审核</span>
              </div>
              <strong>{{ dashboard.review }}<small>项</small></strong>
              <span class="stat-foot">需主管或负责人复核</span>
            </div>
            <div class="stat-card">
              <div class="stat-card-top">
                <span>已完成</span>
              </div>
              <strong>{{ dashboard.completed }}<small>项</small></strong>
              <span class="stat-foot">支持溯源查看与导出</span>
            </div>
          </section>

          <section class="dashboard-grid">
            <div class="panel">
              <div class="section-heading">
                <div>
                  <h3>最近可访问文档</h3>
                </div>
                <el-button text type="primary" @click="navigate('知识文档')"> 全部文档 </el-button>
              </div>
              <div v-if="documents.length" class="document-cards">
                <button
                  v-for="document in documents.slice(0, 6)"
                  :key="document.id"
                  type="button"
                  class="document-card"
                  @click="openDoc(document.id)"
                >
                  <span class="level-pill" :class="`level-${document.level}`">{{
                    levelText(document.level)
                  }}</span>
                  <strong>{{ document.title }}</strong>
                  <small>{{
                    document.scope === 'company' ? '全企业' : document.node_name || document.node_id
                  }}</small>
                </button>
              </div>
              <el-empty v-else description="当前权限范围内暂无文档" />
            </div>

            <div class="panel">
              <div class="section-heading">
                <h3>账号与服务状态</h3>
                <el-button text type="primary" @click="navigate('个人中心')"> 账号设置 </el-button>
              </div>
              <div class="scope-card">
                <span>姓名 / 账号</span>
                <strong>{{ user.display_name }}（{{ user.username }}）</strong>
              </div>
              <div class="scope-card">
                <span>岗位角色</span>
                <strong>{{ displayRoles(user) }}</strong>
              </div>
              <div class="scope-card">
                <span>所属部门</span>
                <strong>{{ primaryOrgName }}</strong>
              </div>
              <div class="scope-card">
                <span>数据权限</span>
                <strong>{{ clearanceText }}</strong>
              </div>
              <div class="scope-card">
                <span>AI 分析服务</span>
                <span
                  class="status-pill"
                  :class="dashboard.model_ready ? 'status-active' : 'status-disabled'"
                >
                  <span class="status-dot" />
                  {{ dashboard.model_ready ? '已就绪' : '未连接' }}
                </span>
              </div>
              <div class="scope-card">
                <span>经营数据集</span>
                <span
                  class="status-pill"
                  :class="dashboard.business_ready ? 'status-active' : 'status-disabled'"
                >
                  <span class="status-dot" />
                  {{ dashboard.business_ready ? '已连接' : '未连接' }}
                </span>
              </div>
            </div>
          </section>
        </template>

        <ResearchChatView v-else-if="currentPage === '选品调研'" :user="user" :api="api" />

        <BiView v-else-if="currentPage === '经营看板'" :user="user" :api="api" />

        <TraceCenter
          v-else-if="currentPage === '轨迹中台' && user.system_admin && !user.must_change"
          :key="user.id"
          :api="api"
        />

        <template v-else-if="currentPage === '知识问答'">
          <section class="question-grid">
            <div class="panel">
              <div class="section-head">
                <div>
                  <h3>企业知识库检索</h3>
                  <p class="section-sub">
                    基于当前账号权限检索企业内部文档，输出结论附带可核验的原文出处。
                  </p>
                </div>
              </div>
              <el-form label-position="top">
                <el-form-item label="咨询问题">
                  <el-input
                    v-model="askForm.question"
                    type="textarea"
                    :rows="5"
                    placeholder="请输入制度流程、合同条款、供应商政策等相关问题..."
                  />
                </el-form-item>
                <div class="form-columns">
                  <el-form-item label="检索部门范围">
                    <el-select v-model="askForm.node_id">
                      <el-option
                        v-for="organization in organizations"
                        :key="organization.id"
                        :label="organization.name"
                        :value="organization.id"
                      />
                    </el-select>
                  </el-form-item>
                  <el-form-item label="数据密级">
                    <div class="account-level-badge">
                      <span class="level-pill" :class="`level-${currentUserLevel}`">
                        {{ levelText(currentUserLevel) }}
                      </span>
                      <span class="level-hint">按当前账号职级匹配</span>
                    </div>
                  </el-form-item>
                </div>
                <el-checkbox v-model="askForm.allow_external">
                  允许非机密文档经外部 AI 模型辅助分析（机密文档默认仅内网处理）
                </el-checkbox>
                <el-alert
                  v-if="currentUserLevel === 3"
                  type="info"
                  :closable="false"
                  show-icon
                  title="机密（L3）文档默认禁止向外部 AI 服务传输，如需使用须经负责人单独授权。"
                />
                <el-button
                  class="submit-question"
                  type="primary"
                  size="large"
                  :loading="busy"
                  @click="submitQuestion"
                >
                  提交查询
                </el-button>
              </el-form>
            </div>

            <div class="panel">
              <h3>检索与审核流程</h3>
              <div class="pipeline-step">
                <span>01</span>
                <div>
                  <strong>权限范围校验</strong>
                  <small>系统自动过滤当前账号所属部门及密级允许访问的文档内容。</small>
                </div>
              </div>
              <div class="pipeline-step">
                <span>02</span>
                <div>
                  <strong>文档提取与核验</strong>
                  <small>提取关键业务条文并比对原文依据，确保答复内容真实可追溯。</small>
                </div>
              </div>
              <div class="pipeline-step">
                <span>03</span>
                <div>
                  <strong>审核与版本留痕</strong>
                  <small>引用结果绑定文档版本号；源文档失效或权限变更时自动停止展示。</small>
                </div>
              </div>
            </div>
          </section>
        </template>

        <TaskCenter
          v-else-if="currentPage === '我的任务' || currentPage === '团队审核'"
          :tasks="visibleTasks"
          :is-review-page="currentPage === '团队审核'"
          :state-text="stateText"
          :state-tag="stateTag"
          @open="openTask"
          @navigate="navigate"
          @reload="load"
        />

        <DocumentManager
          v-else-if="currentPage === '知识文档'"
          v-model:search="search"
          :documents="documents"
          :can-write="capabilities.write"
          :can-manage-permissions="capabilities.permissions"
          :level-text="levelText"
          :state-text="stateText"
          @reload="load"
          @open-permission-id="permissionIdOpen = true"
          @open-request-access="accessOpen = true"
          @new-doc="newDoc"
          @open-doc="openDoc"
        />

        <PolicyMatrix
          v-else-if="currentPage === '权限审批'"
          mode="approvals"
          :approvals="approvals"
          :tree="tree"
          :users="users"
          @decide="decideRequest"
        />

        <PolicyMatrix
          v-else-if="currentPage === '组织与成员'"
          mode="org"
          :approvals="approvals"
          :tree="tree"
          :users="users"
          @new-org="newOrg"
          @edit-org="editOrg"
          @new-account="newAccount"
          @membership="membership"
          @reset-password="openResetDialog"
          @disable-user="disableUser"
        />

        <template v-else-if="currentPage === '操作日志'">
          <section class="panel">
            <div class="section-head">
              <div>
                <h3>操作审计日志</h3>
                <p class="section-sub">记录系统登录、文档访问、权限调整及审批决策历史。</p>
              </div>
            </div>
            <el-table :data="audits" empty-text="暂无操作日志">
              <el-table-column prop="created_at" label="时间" width="195" />
              <el-table-column prop="action" label="操作行为" min-width="200" />
              <el-table-column prop="actor_id" label="操作账号" min-width="150" />
              <el-table-column label="关联部门" min-width="140">
                <template #default="{ row }">
                  {{ orgName(row.node_id) }}
                </template>
              </el-table-column>
              <el-table-column label="密级" width="110">
                <template #default="{ row }">
                  <span class="level-pill" :class="`level-${row.level}`">
                    {{ levelText(row.level) }}
                  </span>
                </template>
              </el-table-column>
            </el-table>
          </section>
        </template>

        <template v-else-if="currentPage === '系统状态'">
          <section class="runtime-grid">
            <div class="panel">
              <h3>服务连接状态</h3>
              <div class="scope-card">
                <span>主数据库与向量检索</span>
                <span
                  class="status-pill"
                  :class="runtimeStatus?.database ? 'status-active' : 'status-disabled'"
                >
                  <span class="status-dot" />
                  {{ runtimeStatus?.database ? '正常' : '异常' }}
                </span>
              </div>
              <div class="scope-card">
                <span>后台任务执行服务</span>
                <span
                  class="status-pill"
                  :class="runtimeStatus?.worker_available ? 'status-active' : 'status-disabled'"
                >
                  <span class="status-dot" />
                  {{ runtimeStatus?.worker_available ? '在线' : '离线' }}
                </span>
              </div>
              <div class="scope-card">
                <span>AI 分析模型配置</span>
                <span
                  class="status-pill"
                  :class="runtimeStatus?.model_configured ? 'status-active' : 'status-disabled'"
                >
                  <span class="status-dot" />
                  {{ runtimeStatus?.model_configured ? '已配置' : '未配置' }}
                </span>
              </div>
              <div class="scope-card">
                <span>经营分析数据源</span>
                <span
                  class="status-pill"
                  :class="runtimeStatus?.business_configured ? 'status-active' : 'status-disabled'"
                >
                  <span class="status-dot" />
                  {{ runtimeStatus?.business_configured ? '已连接' : '未配置' }}
                </span>
              </div>
            </div>

            <div class="panel">
              <h3>任务状态统计</h3>
              <div v-for="c in runtimeStatus?.counts || []" :key="c.state" class="scope-card">
                <span>{{ stateText(c.state) }}</span>
                <strong>{{ c.count }} 项</strong>
              </div>
            </div>
          </section>
        </template>

        <AccountCenter
          v-else-if="currentPage === '个人中心'"
          :user="user"
          :tenant-name="workspaceInfo.name || '企业工作空间'"
          :busy="busy"
          :format-role-name="roleLabel"
          @change-password="changePassword"
          @sign-out="signOut"
        />
      </main>
    </div>
  </div>

  <TaskTimeline
    v-model="taskOpen"
    :task="activeTask"
    @open-doc="openDoc"
    @review="reviewTask"
    @cancel="cancelTask"
    @retry="retryTask"
    @steer="steerTask"
  />

  <el-dialog v-model="docOpen" :title="detail?.title || '文档详情'" width="760px">
    <template v-if="detail">
      <div class="detail-meta">
        <span class="org-chip">
          {{ detail.scope === 'company' ? '全企业' : detail.node_name || detail.node_id }}
        </span>
        <span class="level-pill" :class="`level-${detail.level}`">
          {{ levelText(detail.level) }}
        </span>
        <el-tag size="small" :type="stateTag(detail.state)">{{ stateText(detail.state) }}</el-tag>
        <span>版本 V{{ detail.current_version }}</span>
        <span>权限版本 #{{ detail.acl_version }}</span>
      </div>
      <el-alert v-if="detail.error" type="error" :title="detail.error" :closable="false" />
      <pre v-if="detail.body" class="document-body">{{ detail.body }}</pre>
      <el-empty v-else description="当前仅可查看文档基本信息，暂未获准阅读正文" />
      <div class="dialog-actions">
        <el-link
          v-if="detail.can_download"
          :href="'/api/documents/' + detail.id + '/download'"
          target="_blank"
          type="primary"
        >
          下载原文件
        </el-link>
        <el-button v-if="detail.can_write" @click="editDoc">编辑文档</el-button>
        <el-button v-if="capabilities.permissions" @click="showPolicy">权限与密级</el-button>
        <el-button v-if="capabilities.permissions" @click="showGrant">人员授权</el-button>
        <el-button v-if="detail.body" @click="showVersions">历史版本</el-button>
        <el-button v-if="detail.can_write" type="danger" plain @click="deleteDoc">
          删除文档
        </el-button>
      </div>
    </template>
  </el-dialog>

  <el-dialog v-model="docEdit" :title="docForm.id ? '编辑文档' : '上传 / 新建文档'" width="680px">
    <div class="dialog-lead">
      新增或修改文档后将进入审核流程，审核通过后可供相应权限成员检索查阅。
    </div>
    <el-form label-position="top">
      <el-form-item label="文档标题">
        <el-input v-model="docForm.title" placeholder="请输入文档标题" />
      </el-form-item>
      <div v-if="!docForm.id" class="form-columns">
        <el-form-item label="可见范围">
          <el-select v-model="docForm.scope">
            <el-option v-if="user?.is_boss || capabilities.system" label="全企业" value="company" />
            <el-option label="指定部门" value="org" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="docForm.scope === 'org'" label="所属部门">
          <el-select v-model="docForm.node_id">
            <el-option
              v-for="organization in organizations"
              :key="organization.id"
              :label="organization.name"
              :value="organization.id"
            />
          </el-select>
        </el-form-item>
      </div>
      <div v-if="!docForm.id" class="form-columns">
        <el-form-item label="文档密级">
          <el-select v-model="docForm.level">
            <el-option label="公开 (L1)" :value="1" />
            <el-option label="内部 (L2)" :value="2" />
            <el-option label="机密 (L3)" :value="3" />
          </el-select>
        </el-form-item>
        <el-form-item label="上传本地文件（支持 Markdown / TXT / Word / PDF）">
          <input type="file" @change="picked" />
        </el-form-item>
      </div>
      <el-form-item v-if="!selectedFile" label="文档正文">
        <el-input
          v-model="docForm.body"
          type="textarea"
          :rows="8"
          placeholder="请输入文档正文，或选择本地文件上传"
        />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="docEdit = false">取消</el-button>
      <el-button type="primary" :loading="busy" @click="saveDoc">保存提交</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="policyOpen" title="修改文档权限与密级" width="540px">
    <div class="dialog-lead">
      修改文档密级、可见部门范围或 AI 使用授权需提交企业负责人审批通过后生效。
    </div>
    <el-form label-position="top">
      <div class="form-columns">
        <el-form-item label="可见范围">
          <el-select v-model="policyForm.scope">
            <el-option label="全企业" value="company" />
            <el-option label="指定部门" value="org" />
          </el-select>
        </el-form-item>
        <el-form-item label="文档密级">
          <el-select v-model="policyForm.level">
            <el-option label="公开 (L1)" :value="1" />
            <el-option label="内部 (L2)" :value="2" />
            <el-option label="机密 (L3)" :value="3" />
          </el-select>
        </el-form-item>
      </div>
      <el-form-item v-if="policyForm.scope === 'org'" label="所属部门">
        <el-select v-model="policyForm.node_id">
          <el-option
            v-for="organization in organizations"
            :key="organization.id"
            :label="organization.name"
            :value="organization.id"
          />
        </el-select>
      </el-form-item>
      <el-checkbox v-model="policyForm.ai_allowed">允许授权人员用于 AI 问答分析</el-checkbox>
      <el-checkbox v-model="policyForm.download_allowed">允许具备权限的成员下载原文件</el-checkbox>
    </el-form>
    <template #footer>
      <el-button @click="policyOpen = false">取消</el-button>
      <el-button type="primary" :loading="busy" @click="savePolicy">提交审批</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="grantOpen" title="文档人员授权管理" width="600px">
    <div v-if="currentGrants.length" class="subagents-roster-box" style="margin-top: 0">
      <div class="roster-header">
        <strong>当前有效授权</strong>
      </div>
      <div v-for="g in currentGrants" :key="g.user_id" class="scope-card">
        <span>{{ g.display_name }}</span>
        <strong>
          {{ g.effect === 'deny' ? '禁止访问' : '允许阅读' }}
          {{ g.can_download ? ' · 可下载' : '' }}
          {{ g.expires_at ? ' · 有效期至 ' + g.expires_at : '' }}
        </strong>
      </div>
    </div>
    <el-form label-position="top">
      <el-form-item label="选择成员">
        <el-select v-model="grantForm.user_id" filterable placeholder="请选择企业成员">
          <el-option
            v-for="u in users"
            :key="u.id"
            :label="`${u.display_name} (${u.username})`"
            :value="u.id"
          />
        </el-select>
      </el-form-item>
      <div class="form-columns">
        <el-form-item label="授权类型">
          <el-select v-model="grantForm.effect">
            <el-option label="允许阅读" value="allow" />
            <el-option label="禁止访问" value="deny" />
          </el-select>
        </el-form-item>
        <el-form-item label="有效期至（可选）">
          <el-date-picker
            v-model="grantForm.expires_at"
            type="datetime"
            style="width: 100%"
            placeholder="留空表示长期有效"
          />
        </el-form-item>
      </div>
      <el-checkbox v-model="grantForm.can_download">允许下载原文件</el-checkbox>
      <el-checkbox v-model="grantForm.revoke">收回该成员的文档授权</el-checkbox>
    </el-form>
    <template #footer>
      <el-button @click="grantOpen = false">取消</el-button>
      <el-button type="primary" :loading="busy" @click="saveGrant">提交审批</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="historyOpen" title="历史版本记录" width="660px">
    <el-collapse>
      <el-collapse-item
        v-for="v in history"
        :key="v.version"
        :title="`V${v.version} · ${v.title} (${v.created_at})`"
      >
        <pre class="quote-body">{{ v.body }}</pre>
      </el-collapse-item>
    </el-collapse>
  </el-dialog>

  <el-dialog v-model="accessOpen" title="申请文档阅读权限" width="440px">
    <div class="dialog-lead">
      输入管理员或同事提供的文档 ID 后可直接发起阅读授权申请，申请将流转至对应负责人审批。
    </div>
    <el-form label-position="top">
      <el-form-item label="文档 ID">
        <el-input v-model="accessId" placeholder="请输入文档 ID" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="accessOpen = false">取消</el-button>
      <el-button type="primary" :loading="busy" @click="requestAccess">提交申请</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="permissionIdOpen" title="按文档 ID 管理权限" width="440px">
    <el-form label-position="top">
      <el-form-item label="文档 ID">
        <el-input v-model="permissionId" placeholder="请输入文档 ID" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="permissionIdOpen = false">取消</el-button>
      <el-button type="primary" :loading="busy" @click="manageById">查询配置</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="accountOpen" title="新建成员账号" width="540px">
    <div class="dialog-lead">创建成员账号后，系统将同步提交初始部门分配或管理员角色审批申请。</div>
    <el-form label-position="top">
      <div class="form-columns">
        <el-form-item label="姓名">
          <el-input v-model="accountForm.display_name" placeholder="请输入成员姓名" />
        </el-form-item>
        <el-form-item label="登录账号">
          <el-input v-model="accountForm.username" placeholder="建议使用英文名或工号" />
        </el-form-item>
      </div>
      <el-form-item label="初始密码（建议不少于 12 位）">
        <el-input
          v-model="accountForm.password"
          type="password"
          show-password
          placeholder="请输入初始密码"
        />
      </el-form-item>
      <div class="form-columns">
        <el-form-item label="岗位角色">
          <el-select v-model="accountForm.role">
            <el-option label="普通员工 (L1)" value="employee" />
            <el-option label="部门主管 (L2)" value="leader" />
            <el-option label="企业负责人 (L3)" value="boss" />
            <el-option label="系统管理员" value="admin" />
          </el-select>
        </el-form-item>
        <el-form-item
          v-if="accountForm.role !== 'boss' && accountForm.role !== 'admin'"
          label="所属部门"
        >
          <el-select v-model="accountForm.node_id">
            <el-option
              v-for="organization in organizations"
              :key="organization.id"
              :label="organization.name"
              :value="organization.id"
            />
          </el-select>
        </el-form-item>
      </div>
    </el-form>
    <template #footer>
      <el-button @click="accountOpen = false">取消</el-button>
      <el-button type="primary" :loading="busy" @click="saveAccount">确定新建</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="memberOpen" title="调整成员所属部门" width="480px">
    <div class="dialog-lead">
      成员所属部门及岗位角色变更后，其数据访问范围将随之调整（提交后需审批生效）。
    </div>
    <el-form label-position="top">
      <el-form-item label="岗位角色">
        <el-select v-model="memberForm.role">
          <el-option label="普通员工 (L1)" value="employee" />
          <el-option label="部门主管 (L2)" value="leader" />
          <el-option label="企业负责人 (L3)" value="boss" />
        </el-select>
      </el-form-item>
      <el-form-item v-if="memberForm.role !== 'boss'" label="所属部门">
        <el-select v-model="memberForm.node_id">
          <el-option
            v-for="organization in organizations"
            :key="organization.id"
            :label="organization.name"
            :value="organization.id"
          />
        </el-select>
      </el-form-item>
      <el-checkbox v-model="memberForm.remove">从所选部门中移除该成员</el-checkbox>
    </el-form>
    <template #footer>
      <el-button @click="memberOpen = false">取消</el-button>
      <el-button type="primary" :loading="busy" @click="saveMembership">提交申请</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="resetOpen" title="重置成员登录密码" width="460px">
    <div class="dialog-lead">
      正在为 <strong>{{ resetForm.display_name }}</strong
      >（{{ resetForm.username }}）申请重置密码。审批生效后该账号现有登录会话将立即失效。
    </div>
    <el-form label-position="top" @submit.prevent="submitResetPassword">
      <el-form-item label="新临时密码（建议不少于 12 位）">
        <el-input
          v-model="resetForm.new_password"
          type="password"
          show-password
          placeholder="请输入新的临时登录密码"
        />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="resetOpen = false">取消</el-button>
      <el-button type="primary" :loading="busy" @click="submitResetPassword">提交申请</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="orgOpen" :title="orgForm.id ? '编辑部门' : '新建部门'" width="460px">
    <el-form label-position="top">
      <el-form-item label="部门名称">
        <el-input v-model="orgForm.name" placeholder="例如：华东电商运营部" />
      </el-form-item>
      <el-form-item v-if="!orgForm.id" label="组织类型">
        <el-select v-model="orgForm.kind">
          <el-option label="部门" value="department" />
          <el-option label="业务小组" value="team" />
        </el-select>
      </el-form-item>
      <el-form-item label="上级部门">
        <el-select v-model="orgForm.parent_id">
          <el-option
            v-for="organization in organizations.filter((item) => item.id !== orgForm.id)"
            :key="organization.id"
            :label="organization.name"
            :value="organization.id"
          />
        </el-select>
      </el-form-item>
      <el-checkbox v-if="orgForm.id" v-model="orgForm.active">启用该部门</el-checkbox>
    </el-form>
    <template #footer>
      <el-button @click="orgOpen = false">取消</el-button>
      <el-button type="primary" :loading="busy" @click="saveOrg">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { requestApi } from './api'
import { openTaskEventStream } from './composables/useTaskStream'
import { levelText, stateTag, stateText } from './labels'
import { workspaceNavigation, workspacePages, type WorkspacePage } from './workspaceNavigation'
import AccountCenter from './views/AccountCenter.vue'
import BiView from './views/BiView.vue'
import DocumentManager from './views/DocumentManager.vue'
import LoginPortal from './views/LoginPortal.vue'
import PolicyMatrix from './views/PolicyMatrix.vue'
import ResearchChatView from './views/ResearchChatView.vue'
import TaskCenter from './views/TaskCenter.vue'
import TaskTimeline from './views/TaskTimeline.vue'
import TraceCenter from './views/TraceCenter.vue'
import type {
  Approval,
  AuditEvent,
  Capabilities,
  CreatedResource,
  Dashboard,
  DocumentListItem,
  DocumentRecord,
  DocumentVersion,
  Grant,
  Items,
  LoginResponse,
  Organization,
  OrganizationTree,
  QuickAccount,
  RuntimeStatus,
  Task,
  User,
  WorkspaceInfo,
} from './types'

const workspaceInfo = ref<WorkspaceInfo>({
  name: '',
  mode: '',
  tenant_code: '',
  quick_accounts: [],
})
const user = ref<User | null>(null)
const currentPage = ref<WorkspacePage>('工作概览')
const busy = ref(false)
const search = ref('')
const organizations = ref<Organization[]>([])
const documents = ref<DocumentListItem[]>([])
const tasks = ref<Task[]>([])
const approvals = ref<Approval[]>([])
const users = ref<User[]>([])
const audits = ref<AuditEvent[]>([])
const runtimeStatus = ref<RuntimeStatus | null>(null)
const dashboard = ref<Dashboard>({
  documents: 0,
  tasks: 0,
  review: 0,
  completed: 0,
  teams: [],
  model_ready: false,
  business_ready: false,
})

const loginForm = reactive({ username: '', password: '' })
const askForm = reactive({ question: '', node_id: '', input_level: 1, allow_external: false })

const docOpen = ref(false)
const detail = ref<DocumentRecord | null>(null)
const docEdit = ref(false)
const docForm = reactive({
  id: '',
  title: '',
  body: '',
  scope: 'org',
  node_id: '',
  level: 2,
  version: 1,
})
const selectedFile = ref<File | null>(null)

const policyOpen = ref(false)
const policyForm = reactive({
  scope: 'org',
  node_id: '',
  level: 2,
  ai_allowed: false,
  download_allowed: false,
})

const grantOpen = ref(false)
const currentGrants = ref<Grant[]>([])
const grantForm = reactive({
  user_id: '',
  effect: 'allow',
  can_download: false,
  expires_at: '',
  revoke: false,
})

const historyOpen = ref(false)
const history = ref<DocumentVersion[]>([])
const accessOpen = ref(false)
const accessId = ref('')
const permissionIdOpen = ref(false)
const permissionId = ref('')

const taskOpen = ref(false)
const activeTask = ref<Task | null>(null)

const accountOpen = ref(false)
const accountForm = reactive({
  username: '',
  password: '',
  display_name: '',
  role: 'employee',
  node_id: '',
})

const memberOpen = ref(false)
const memberForm = reactive({ user_id: '', node_id: '', role: 'employee', remove: false })

const resetOpen = ref(false)
const resetForm = reactive({ user_id: '', username: '', display_name: '', new_password: '' })

const orgOpen = ref(false)
const orgForm = reactive({ id: '', name: '', kind: 'department', parent_id: '', active: true })

/**
 * 判断审批记录是否处于待处理状态。
 * @param row - 审批记录。
 * @returns 是否为待审批状态。
 */
function isPendingApproval(row: Approval): boolean {
  return row.state === 'pending'
}

/**
 * 读取当前登录身份的功能能力集合。
 * @returns 权限能力对象。
 */
function resolveCapabilities(): Capabilities {
  return (
    user.value?.capabilities || {
      system: false,
      write: false,
      review: false,
      permissions: false,
    }
  )
}
const capabilities = computed(resolveCapabilities)

/**
 * 计算组织根节点编号。
 * @returns 公司根节点 ID。
 */
function resolveRootOrganizationId(): string {
  for (const organization of organizations.value) {
    if (organization.kind === 'company') return organization.id
  }
  return organizations.value[0]?.id || ''
}
const rootOrganizationId = computed(resolveRootOrganizationId)

/**
 * 将组织平铺列表构造成前端树形控件结构。
 * @param parentId - 父级组织 ID。
 * @returns 递归嵌套组织树节点数组。
 */
function buildTree(parentId: string | null): OrganizationTree[] {
  const result: OrganizationTree[] = []
  for (const organization of organizations.value) {
    if (organization.parent_id !== parentId) continue
    result.push({ ...organization, label: organization.name, children: buildTree(organization.id) })
  }
  return result
}
const tree = computed(
  /**
   * 生成根节点起点的企业组织架构树。
   * @returns 组织树。
   */
  () => buildTree(null),
)

const pendingApprovalCount = computed(() => approvals.value.filter(isPendingApproval).length)
const navigationGroups = computed(() =>
  workspaceNavigation(
    user.value,
    capabilities.value,
    pendingApprovalCount.value,
    dashboard.value.review,
  ),
)
const currentPageDefinition = computed(() => workspacePages[currentPage.value])

/**
 * 过滤当前任务视图（我的任务仅展示本人发起的任务，团队审核展示全部待审任务）。
 * @returns 当前页面可见任务列表。
 */
function computeVisibleTasks(): Task[] {
  if (currentPage.value === '团队审核') return tasks.value
  const currentUserId = user.value?.id
  return currentUserId ? tasks.value.filter((task) => task.owner_id === currentUserId) : tasks.value
}
const visibleTasks = computed(computeVisibleTasks)

/**
 * 获取当前登录用户的首字缩写。
 * @returns 大写头像缩写。
 */
function computeUserInitial(): string {
  const raw = (user.value?.display_name || user.value?.username || 'U').trim()
  return raw ? raw.slice(0, 1).toUpperCase() : 'U'
}
const userInitial = computed(computeUserInitial)

/**
 * 计算当前用户的角色视觉配色类名。
 * @returns 样式类名。
 */
function computeUserTone(): string {
  if (user.value?.is_boss) return 'tone-exec'
  if (user.value?.is_leader) return 'tone-lead'
  if (user.value?.system_admin) return 'tone-admin'
  return 'tone-member'
}
const userToneClass = computed(computeUserTone)

/**
 * 获取当前用户主归属部门名称。
 * @returns 部门名称。
 */
function computePrimaryOrg(): string {
  const membership = user.value?.memberships?.[0]
  return membership?.name || orgName(membership?.node_id) || '暂未分配部门'
}
const primaryOrgName = computed(computePrimaryOrg)

/**
 * 将组织节点编号解析为可读部门名称。
 * @param nodeId 组织节点编号。
 * @returns 部门名称，未命中时回退编号或占位符。
 */
function orgName(nodeId?: string | null): string {
  if (!nodeId) return '—'
  return organizations.value.find((organization) => organization.id === nodeId)?.name || nodeId
}

/**
 * 推导当前账户的数据访问密级数值（L1/L2/L3）。
 * @returns 密级数字。
 */
function computeUserLevel(): number {
  if (user.value?.is_boss) return 3
  if (user.value?.is_leader) return 2
  return 1
}
const currentUserLevel = computed(computeUserLevel)

const clearanceText = computed(
  /**
   * 输出完整的数据权限等级文本。
   * @returns 密级名称。
   */
  () => levelText(currentUserLevel.value),
)

const clearanceShortText = computed(
  /**
   * 输出侧栏底部精简密级标识。
   * @returns 精简密级标签。
   */
  () =>
    `${currentUserLevel.value === 3 ? '机密 (L3)' : currentUserLevel.value === 2 ? '内部 (L2)' : '公开 (L1)'}`,
)

/**
 * 将组织角色编码映射为规范企业岗位称谓。
 * @param role - 服务端角色编码。
 * @returns 中文岗位名称。
 */
function roleLabel(role: string): string {
  const map: Record<string, string> = {
    employee: '普通员工',
    leader: '部门主管',
    boss: '企业负责人',
    admin: '系统管理员',
  }
  return map[role] || role
}

/**
 * 格式化用户信息条展示的岗位角色摘要。
 * @param row - 账号对象。
 * @returns 角色摘要文案。
 */
function displayRoles(row: User): string {
  const items: string[] = []
  if (row.is_boss) items.push('企业负责人')
  else if (row.is_leader) items.push('部门主管')
  else items.push('普通员工')
  if (row.system_admin) items.push('系统管理员')
  return items.join(' · ')
}

/**
 * 清空客户端所有业务状态，确保退出或改密后不残留会话信息。
 */
function clearSession(): void {
  user.value = null
  organizations.value = []
  documents.value = []
  tasks.value = []
  approvals.value = []
  users.value = []
  audits.value = []
  runtimeStatus.value = null
  detail.value = null
  activeTask.value = null
  docOpen.value = false
  taskOpen.value = false
}

/**
 * 调用同源企业服务端 API，统一注入运行环境 CSRF 与未认证会话清理回调。
 * @param path - 同源请求路径。
 * @param method - HTTP 方法，默认 GET。
 * @param payload - JSON 对象或 FormData 表单载体。
 * @returns 经过结构校验的服务端响应数据。
 */
async function api<T>(path: string, method = 'GET', payload?: unknown): Promise<T> {
  return requestApi<T>(path, workspaceInfo.value.mode, clearSession, method, payload)
}

/**
 * 将未知异常安全提取为中文错误提示文本。
 * @param e - 捕获到的异常对象。
 * @returns 面向用户的中文错误提示。
 */
function readError(e: unknown): string {
  return e instanceof Error ? e.message : '操作未完成，请稍后重试'
}

/**
 * 包装异步操作，统一维护加载锁与中文异常提示。
 * @param fn - 需要执行的业务操作。
 */
async function action(fn: () => Promise<void>): Promise<void> {
  busy.value = true
  try {
    await fn()
  } catch (e: unknown) {
    ElMessage.error(readError(e))
  } finally {
    busy.value = false
  }
}

/**
 * 并行刷新当前账号权限范围内的数据快照。
 */
async function load(): Promise<void> {
  if (!user.value) return
  if (user.value.must_change) {
    currentPage.value = '个人中心'
    return
  }
  const query = search.value ? '?q=' + encodeURIComponent(search.value) : ''
  const [overview, organizationPage, documentPage, taskPage, approvalPage, userPage] =
    await Promise.all([
      api<Dashboard>('/dashboard'),
      api<Items<Organization>>('/organizations'),
      api<Items<DocumentListItem>>('/documents' + query),
      api<Items<Task>>('/tasks'),
      capabilities.value.permissions
        ? api<Items<Approval>>('/approvals')
        : Promise.resolve({ items: [] as Approval[] }),
      capabilities.value.system || capabilities.value.permissions
        ? api<Items<User>>('/users')
        : Promise.resolve({ items: [] as User[] }),
    ])
  dashboard.value = overview
  organizations.value = organizationPage.items
  documents.value = documentPage.items
  tasks.value = taskPage.items
  approvals.value = approvalPage.items
  users.value = userPage.items
  if (!askForm.node_id && organizations.value.length) askForm.node_id = organizations.value[0].id
}

/**
 * 使用企业账号登录并设置默认密级与外部分析开关；登录成功后立即清空密码输入。
 */
async function signIn(): Promise<void> {
  await action(async () => {
    const result = await api<LoginResponse>('/auth/login', 'POST', {
      ...loginForm,
      tenant_code: workspaceInfo.value.tenant_code,
    })
    const signedUser = result.user
    user.value = signedUser
    loginForm.password = ''
    currentPage.value = signedUser.must_change ? '个人中心' : '工作概览'
    askForm.input_level = signedUser.is_boss ? 3 : signedUser.is_leader ? 2 : 1
    askForm.allow_external = !signedUser.is_boss
    await load()
  })
}

/**
 * 退出当前账号并清空客户端状态。
 */
async function signOut(): Promise<void> {
  await action(async () => {
    await api('/auth/logout', 'POST')
    clearSession()
    loginForm.username = ''
    ElMessage.success('已退出登录')
  })
}

/**
 * 快捷填充测试账号凭据。
 * @param acc - 服务端返回的测试账号元数据。
 */
function pickAccount(acc: QuickAccount): void {
  loginForm.username = acc.username
  if (acc.password) loginForm.password = acc.password
}

/**
 * 处理右上角用户菜单指令。
 * @param cmd - 菜单命令标识。
 */
function handleUserCommand(cmd: string): void {
  if (cmd === 'account') void navigate('个人中心')
  else if (cmd === 'tasks') void navigate('我的任务')
  else if (cmd === 'signout') void signOut()
}

/**
 * 切换菜单并加载对应页面数据。
 * @param name - 菜单名称。
 */
async function navigate(name: string): Promise<void> {
  const destination = navigationGroups.value
    .flatMap((group) => group.items)
    .find((item) => item.page === name)
  if (!destination) return
  currentPage.value = destination.page
  if (name === '轨迹中台') return
  if (name === '系统状态') {
    await action(async () => {
      runtimeStatus.value = await api<RuntimeStatus>('/system/status')
    })
  } else if (name === '操作日志') {
    await action(async () => {
      audits.value = (await api<Items<AuditEvent>>('/audit')).items
    })
  } else {
    await action(load)
  }
}

/**
 * 提交知识库问答任务并开启实时进度监听。
 */
async function submitQuestion(): Promise<void> {
  if (!askForm.question.trim()) {
    ElMessage.warning('请先填写咨询问题')
    return
  }
  askForm.input_level = currentUserLevel.value
  await action(async () => {
    const r = await api<CreatedResource>('/tasks', 'POST', askForm)
    activeTask.value = await api<Task>('/tasks/' + r.id)
    taskOpen.value = true
    streamTask(r.id)
    ElMessage.success('任务已提交')
    await load()
  })
}

let closeTaskStream: (() => void) | null = null

/**
 * 订阅指定任务的服务端 SSE 进度流，连接异常时静默降级为轮询。
 * @param id - 任务 ID。
 */
function streamTask(id: string): void {
  closeTaskStream?.()
  closeTaskStream = openTaskEventStream(id, {
    /**
     * 处理实时阶段推送。
     * @param row - 服务端推送的任务进展条目。
     */
    onMessage: (row) => {
      if (!activeTask.value || activeTask.value.id !== id) return
      if (row.stage) activeTask.value.stage = row.stage
      if (typeof row.progress === 'number') activeTask.value.progress = row.progress
      if (!activeTask.value.events) activeTask.value.events = []
      const exists = activeTask.value.events.some((e) => e.stage === row.stage)
      if (row.stage && !exists) {
        activeTask.value.events.push({
          id: row.id || Date.now(),
          stage: row.stage,
          progress: row.progress || 0,
          status: row.status || 'running',
        })
      }
    },
    /**
     * 监听终态事件并全量刷新任务结论。
     * @param done - 结束事件携带的任务标识。
     */
    onDone: (done) => {
      closeTaskStream = null
      const doneId = done.id
      if (doneId && activeTask.value?.id === doneId) {
        api<Task>('/tasks/' + doneId).then(
          /**
           * 回写终态任务明细。
           * @param t - 最新任务对象。
           */
          (t: Task) => {
            if (activeTask.value?.id === doneId) activeTask.value = t
          },
        )
      }
    },
    /** 连接异常时清空句柄，保留轮询兜底刷新。 */
    onError: () => {
      closeTaskStream = null
    },
  })
}

/**
 * 加载并展示指定任务详情。
 * @param id - 任务 ID。
 */
async function openTask(id: string): Promise<void> {
  await action(async () => {
    activeTask.value = await api<Task>('/tasks/' + id)
    taskOpen.value = true
  })
}

/**
 * 提交审核决定。
 * @param approveOrTaskId - 审核决定或任务 ID。
 * @param maybeApprove - 当首参为 ID 时的审核布尔决定。
 */
async function reviewTask(
  approveOrTaskId: boolean | string,
  maybeApprove?: boolean,
): Promise<void> {
  if (!activeTask.value) return
  const approve = typeof approveOrTaskId === 'boolean' ? approveOrTaskId : Boolean(maybeApprove)
  await action(async () => {
    await api('/tasks/' + activeTask.value!.id + '/review', 'POST', { approve, comment: '' })
    ElMessage.success(approve ? '已通过并发布' : '已驳回')
    activeTask.value = await api<Task>('/tasks/' + activeTask.value!.id)
    await load()
  })
}

/**
 * 取消正在等待或执行的任务。
 * @param taskId - 目标任务 ID。
 */
async function cancelTask(taskId?: string): Promise<void> {
  const tid = taskId || activeTask.value?.id
  if (!tid) return
  await action(async () => {
    await api('/tasks/' + tid + '/cancel', 'POST')
    ElMessage.success('已申请取消任务')
    await load()
  })
}

/**
 * 重新发起任务执行。
 * @param taskId - 目标任务 ID。
 */
async function retryTask(taskId?: string): Promise<void> {
  const tid = taskId || activeTask.value?.id
  if (!tid) return
  await action(async () => {
    await api('/tasks/' + tid + '/retry', 'POST')
    ElMessage.success('已重新发起任务')
    await load()
  })
}

/**
 * 向运行中任务发送补充要求。
 * @param taskId - 目标任务 ID。
 * @param message - 补充要求正文。
 */
async function steerTask(taskId: string, message: string): Promise<void> {
  if (!taskId || !message.trim()) return
  await action(async () => {
    await api('/tasks/' + taskId + '/steer', 'POST', { message: message.trim() })
    ElMessage.success('补充要求已发送')
    await load()
  })
}

/**
 * 查看授权文档详情。
 * @param id - 文档 ID。
 */
async function openDoc(id: string): Promise<void> {
  await action(async () => {
    detail.value = await api<DocumentRecord>('/documents/' + id)
    docOpen.value = true
  })
}

/**
 * 初始化新建文档表单。
 */
function newDoc(): void {
  Object.assign(docForm, {
    id: '',
    title: '',
    body: '',
    scope: user.value?.system_admin && !user.value?.is_boss ? 'company' : 'org',
    node_id:
      user.value?.system_admin && !user.value?.is_boss
        ? rootOrganizationId.value
        : user.value?.memberships[0]?.node_id || rootOrganizationId.value,
    level: user.value?.system_admin && !user.value?.is_boss ? 1 : 2,
    version: 1,
  })
  selectedFile.value = null
  docEdit.value = true
}

/**
 * 载入现有文档版本准备编辑。
 */
function editDoc(): void {
  if (!detail.value) return
  Object.assign(docForm, {
    id: detail.value.id,
    title: detail.value.title,
    body: detail.value.body,
    scope: detail.value.scope,
    node_id: detail.value.node_id,
    level: detail.value.level,
    version: detail.value.current_version,
  })
  selectedFile.value = null
  docEdit.value = true
}

/**
 * 保存新建文档或更新版本。
 */
async function saveDoc(): Promise<void> {
  await action(async () => {
    if (docForm.id) {
      await api('/documents/' + docForm.id, 'PUT', {
        title: docForm.title,
        body: docForm.body,
        version: docForm.version,
      })
    } else if (selectedFile.value) {
      const form = new FormData()
      form.append('file', selectedFile.value)
      for (const k of ['title', 'scope', 'node_id', 'level'] as const) {
        form.append(k, String(docForm[k]))
      }
      await api('/documents/upload', 'POST', form)
    } else {
      await api('/documents', 'POST', {
        title: docForm.title,
        body: docForm.body,
        node_id: docForm.scope === 'company' ? rootOrganizationId.value : docForm.node_id,
        scope: docForm.scope,
        level: docForm.level,
      })
    }
    docEdit.value = false
    docOpen.value = false
    ElMessage.success('文档已保存，等待审核发布')
    await load()
  })
}

/**
 * 读取选择的文件对象。
 * @param e - 文件选择器事件。
 */
function picked(e: Event): void {
  selectedFile.value = (e.target as HTMLInputElement).files?.[0] || null
}

/**
 * 确认后删除当前文档。
 */
async function deleteDoc(): Promise<void> {
  if (!detail.value) return
  await action(async () => {
    await ElMessageBox.confirm('删除后，引用该文档的历史答案将受限展示，确认删除？', '删除文档', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await api('/documents/' + detail.value!.id, 'DELETE')
    docOpen.value = false
    ElMessage.success('文档已删除')
    await load()
  })
}

/**
 * 打开文档权限与密级设置弹窗。
 */
function showPolicy(): void {
  if (!detail.value) return
  Object.assign(policyForm, {
    scope: detail.value.scope,
    node_id: detail.value.node_id,
    level: detail.value.level,
    ai_allowed: detail.value.ai_allowed,
    download_allowed: detail.value.download_allowed,
  })
  policyOpen.value = true
}

/**
 * 提交文档权限调整申请。
 */
async function savePolicy(): Promise<void> {
  await action(async () => {
    await api('/documents/' + detail.value!.id + '/policy', 'POST', {
      ...policyForm,
      node_id: policyForm.scope === 'company' ? rootOrganizationId.value : policyForm.node_id,
    })
    policyOpen.value = false
    ElMessage.success('权限变更已提交，等待企业负责人审批')
    await load()
  })
}

/**
 * 打开单文档人员授权窗口。
 */
async function showGrant(): Promise<void> {
  currentGrants.value = []
  await action(async () => {
    currentGrants.value = (
      await api<Items<Grant>>('/documents/' + detail.value!.id + '/grants')
    ).items
  })
  Object.assign(grantForm, {
    user_id: '',
    effect: 'allow',
    can_download: false,
    expires_at: '',
    revoke: false,
  })
  grantOpen.value = true
}

/**
 * 提交单文档人员授权申请。
 */
async function saveGrant(): Promise<void> {
  await action(async () => {
    await api('/documents/' + detail.value!.id + '/grants', 'POST', {
      ...grantForm,
      expires_at: grantForm.expires_at ? new Date(grantForm.expires_at).toISOString() : null,
    })
    grantOpen.value = false
    ElMessage.success('授权申请已提交审批')
    await load()
  })
}

/**
 * 加载获准查阅的历史版本列表。
 */
async function showVersions(): Promise<void> {
  await action(async () => {
    history.value = (
      await api<Items<DocumentVersion>>('/documents/' + detail.value!.id + '/versions')
    ).items
    historyOpen.value = true
  })
}

/**
 * 审批或驳回待处理申请。
 * @param row - 审批记录。
 * @param approve - 是否通过。
 */
async function decideRequest(row: Approval, approve: boolean): Promise<void> {
  await action(async () => {
    await api('/approvals/' + row.id + '/decision', 'POST', { approve, comment: '' })
    ElMessage.success(approve ? '已审批通过' : '已驳回申请')
    await load()
  })
}

/**
 * 初始化新建成员账号表单。
 */
function newAccount(): void {
  Object.assign(accountForm, {
    username: '',
    password: '',
    display_name: '',
    role: 'employee',
    node_id: rootOrganizationId.value,
  })
  accountOpen.value = true
}

/**
 * 新建企业成员账号并提交初始部门/角色审批。
 */
async function saveAccount(): Promise<void> {
  await action(async () => {
    const r = await api<CreatedResource>('/users', 'POST', {
      username: accountForm.username,
      password: accountForm.password,
      display_name: accountForm.display_name,
    })
    if (accountForm.role === 'admin') {
      await api('/users/' + r.id, 'PATCH', { system_admin: true })
    } else {
      await api('/users/' + r.id + '/memberships', 'POST', {
        node_id: accountForm.role === 'boss' ? rootOrganizationId.value : accountForm.node_id,
        role: accountForm.role,
        remove: false,
      })
    }
    accountOpen.value = false
    accountForm.password = ''
    ElMessage.success('成员账号已创建，部门分配待审批')
    await load()
  })
}

/**
 * 打开成员部门调整弹窗。
 * @param row - 目标成员对象。
 */
function membership(row: User): void {
  Object.assign(memberForm, {
    user_id: row.id,
    node_id: row.memberships?.[0]?.node_id || rootOrganizationId.value,
    role: row.memberships?.[0]?.role || 'employee',
    remove: false,
  })
  memberOpen.value = true
}

/**
 * 提交成员部门调整或移出申请。
 */
async function saveMembership(): Promise<void> {
  await action(async () => {
    await api('/users/' + memberForm.user_id + '/memberships', 'POST', {
      node_id: memberForm.role === 'boss' ? rootOrganizationId.value : memberForm.node_id,
      role: memberForm.role,
      remove: memberForm.remove,
    })
    memberOpen.value = false
    ElMessage.success('部门调整申请已提交')
    await load()
  })
}

/**
 * 确认后申请停用或启用成员账号。
 * @param row - 目标成员对象。
 */
async function disableUser(row: User): Promise<void> {
  await action(async () => {
    const targetAction = row.active ? '停用' : '启用'
    await ElMessageBox.confirm(
      `确定申请${targetAction}成员「${row.display_name}」(${row.username}) 吗？账号停用后将自动注销其当前登录会话。`,
      `${targetAction}账号`,
      { confirmButtonText: `确定${targetAction}`, cancelButtonText: '取消', type: 'warning' },
    )
    await api('/users/' + row.id, 'PATCH', { active: !row.active })
    ElMessage.success(`账号${targetAction}申请已提交审批`)
    await load()
  })
}

/**
 * 打开重置成员密码弹窗。
 * @param row - 目标成员对象。
 */
function openResetDialog(row: User): void {
  Object.assign(resetForm, {
    user_id: row.id,
    username: row.username,
    display_name: row.display_name,
    new_password: '',
  })
  resetOpen.value = true
}

/**
 * 提交重置临时密码审批请求。
 */
async function submitResetPassword(): Promise<void> {
  if (!resetForm.new_password) {
    ElMessage.warning('请输入新的临时密码')
    return
  }
  await action(async () => {
    await api('/users/' + resetForm.user_id, 'PATCH', { new_password: resetForm.new_password })
    resetForm.new_password = ''
    resetOpen.value = false
    ElMessage.success('密码重置申请已提交，等待审批')
    await load()
  })
}

/**
 * 初始化新建部门表单。
 */
function newOrg(): void {
  Object.assign(orgForm, {
    id: '',
    name: '',
    kind: 'department',
    parent_id: rootOrganizationId.value,
    active: true,
  })
  orgOpen.value = true
}

/**
 * 载入现有部门信息准备编辑。
 * @param row - 目标部门对象。
 */
function editOrg(row: Organization): void {
  Object.assign(orgForm, {
    id: row.id,
    name: row.name,
    kind: row.kind,
    parent_id: row.parent_id,
    active: row.active,
  })
  orgOpen.value = true
}

/**
 * 提交部门新建或调整申请。
 */
async function saveOrg(): Promise<void> {
  await action(async () => {
    if (orgForm.id) {
      await api('/organizations/' + orgForm.id, 'PATCH', {
        name: orgForm.name,
        parent_id: orgForm.parent_id,
        active: orgForm.active,
      })
    } else {
      await api('/organizations', 'POST', {
        name: orgForm.name,
        kind: orgForm.kind,
        parent_id: orgForm.parent_id,
      })
    }
    orgOpen.value = false
    ElMessage.success(orgForm.id ? '部门调整申请已提交' : '部门已创建')
    await load()
  })
}

/**
 * 按文档 ID 提交阅读权限申请。
 */
async function requestAccess(): Promise<void> {
  await action(async () => {
    await api('/documents/' + encodeURIComponent(accessId.value) + '/request-access', 'POST')
    accessOpen.value = false
    ElMessage.success('访问申请已提交')
  })
}

/**
 * 管理员按文档 ID 查询并配置权限策略。
 */
async function manageById(): Promise<void> {
  await action(async () => {
    detail.value = await api<DocumentRecord>(
      '/documents/' + encodeURIComponent(permissionId.value) + '/permission-status',
    )
    permissionIdOpen.value = false
    showPolicy()
  })
}

/**
 * 个人修改登录密码并强制重新登录。
 * @param payload - 包含当前密码与新密码的变更载荷。
 * @param payload.current_password - 当前登录密码。
 * @param payload.new_password - 新登录密码。
 */
async function changePassword(payload: {
  current_password: string
  new_password: string
}): Promise<void> {
  await action(async () => {
    await api('/auth/password', 'POST', payload)
    clearSession()
    ElMessage.success('密码已修改，请重新登录')
  })
}

let timer: ReturnType<typeof setInterval>

onMounted(async () => {
  try {
    workspaceInfo.value = await api<WorkspaceInfo>('/info')
  } catch (e: unknown) {
    console.error('获取工作空间信息失败:', e)
  }
  try {
    const me = await api<User>('/auth/me')
    user.value = me
    askForm.input_level = me.is_boss ? 3 : me.is_leader ? 2 : 1
    askForm.allow_external = !me.is_boss
    await load()
  } catch {}
  timer = setInterval(async () => {
    if (!user.value || user.value.must_change) return
    try {
      if (taskOpen.value && activeTask.value) {
        activeTask.value = await api<Task>('/tasks/' + activeTask.value.id)
      }
      if (docOpen.value && detail.value) {
        try {
          detail.value = await api<DocumentRecord>('/documents/' + detail.value.id)
        } catch {
          docOpen.value = false
          detail.value = null
        }
      }
    } catch {}
  }, 2500)
})

onUnmounted(() => {
  clearInterval(timer)
  closeTaskStream?.()
})
</script>
