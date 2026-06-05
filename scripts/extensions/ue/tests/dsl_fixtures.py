# audience: internal
# extensions.ue.tests.dsl_fixtures
"""UE DSL 校验测试的共享夹具：合法 S 表达式样例与错误码收集器。

GOOD_MATLANG / GOOD_BPLISP 是结构套件与语义套件共用的同一份样例（曾各自内联抄写）。
animlang 有两份有意不同的样例：GOOD_ANIMLANG_STRUCT 不带 ``:transitions`` 块（结构套件用），
GOOD_ANIMLANG_SEMANTIC 带 ``:transitions``（语义套件用其 ``:condition``/``:duration`` 触发 schema 趟）。
``codes`` 是各套件共用的错误码收集器：跑 ``dsl_validate`` 取错误码、可选按严重度过滤、排序返回；
``dsl_validate`` 在函数体内惰性导入，使 tree-sitter 缺席时本模块仍可导入（测试由 pytestmark 跳过）。
"""

#### 合法 matlang 内联夹具（真实 simple_pbr 示例的镜像，期望零错误） [@380kkm 2026-06-05] ####
GOOD_MATLANG = (
    '(material "M_SimplePBR"\n'
    "  :domain surface\n"
    "  (expressions\n"
    "    (texture-sample $tex1 :uv (connect $uv1))\n"
    "    (texture-coordinate $uv1 :coordinate-index 0)\n"
    '    (vector-parameter $vparam1 :name "TintColor")\n'
    "    (multiply $mul1 :a (connect $tex1 0) :b (connect $vparam1 0))\n"
    "    (constant $const1 :value 0.0))\n"
    "  (outputs\n"
    "    :base-color (connect $mul1 0)\n"
    "    :metallic (connect $const1 0)))\n"
)

#### 合法 bplisp 内联夹具（villager 示例的镜像，期望零错误） [@380kkm 2026-06-05] ####
GOOD_BPLISP = (
    "(function\n"
    "  None\n"
    '  :event-id "8abce957"\n'
    "  :param (Selected Actor)\n"
    '  (PrintString :instring "Villager Select called!" :id "5f6936c3")\n'
    '  (set Selected "K2Node_FunctionEntry" :id "226de0c6")\n'
    "  (let returnvalue\n"
    '    (SpawnSystemAttached :location "0, 0, 0" :id "60944b57")))\n'
)

#### 合法 animlang 内联夹具（state_machine 镜像，无 :transitions 块，结构套件用） [@380kkm 2026-06-05] ####
GOOD_ANIMLANG_STRUCT = (
    '(anim-blueprint "SimpleStateMachine"\n'
    "  :variables [(float :speed 0.0 :range [0.0 600.0])]\n"
    "  :anim-graph\n"
    "    (state-machine :locomotion :initial :idle\n"
    "      :states\n"
    '        [(state :idle (sequence-player "Idle_Rifle" :loop true))\n'
    '         (state :walk (sequence-player "Walk_Fwd" :loop true))]))\n'
)

#### 合法 animlang 内联夹具（state_machine 镜像，带 :transitions 块，语义套件用） [@380kkm 2026-06-05] ####
GOOD_ANIMLANG_SEMANTIC = (
    '(anim-blueprint "SimpleStateMachine"\n'
    "  :variables [(float :speed 0.0 :range [0.0 600.0])]\n"
    "  :anim-graph\n"
    "    (state-machine :locomotion :initial :idle\n"
    "      :states\n"
    '        [(state :idle (sequence-player "Idle_Rifle" :loop true))\n'
    '         (state :walk (sequence-player "Walk_Fwd" :loop true))]\n'
    "      :transitions\n"
    "        [(transition :idle :walk :condition (> :speed 10.0) :duration 0.2)]))\n"
)


#### 收集校验结果的错误码（可选带 schema、可选按严重度过滤），排序返回 [@380kkm 2026-06-05] ####
def codes(text, lang, schema=None, sev=None):
    import dsl_validate as V
    return sorted(i.code for i in V.dsl_validate(text, lang, schema)
                  if sev is None or i.severity == sev)
