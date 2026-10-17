# Offline independent references. Never run in CI; pass an explicit raw directory.
# Uses PowerModels equations, not translated cvxopf network expressions.
using PowerModels, JuMP, Clarabel, JSON, SHA, Pkg

const PM = PowerModels
const ROOT = @__DIR__
length(ARGS) == 1 || error("Usage: generate_references.jl RAW_DIRECTORY")
const RAW = abspath(only(ARGS))

function check_parser(data, original)
    base = original["baseMVA"]
    @assert data["per_unit"] && data["baseMVA"] == base
    @assert length(data["bus"]) == length(original["bus"])
    @assert length(data["gen"]) == length(original["gen"])
    @assert length(data["branch"]) == length(original["branch"])
    near(a, b) = @assert isapprox(a, b; atol=1e-12, rtol=1e-12) (a, b)
    for row in original["bus"]
        id = Int(row[1]); b = data["bus"][string(id)]
        @assert b["bus_i"] == id && b["bus_type"] == row[2] && row[2] != 4
        near(b["vmax"], row[12]); near(b["vmin"], row[13])
        for (table, buskey, key, index) in (
            ("load", "load_bus", "pd", 3), ("load", "load_bus", "qd", 4),
            ("shunt", "shunt_bus", "gs", 5), ("shunt", "shunt_bus", "bs", 6))
            entries = [item for item in values(data[table]) if item[buskey] == id]
            @assert all(item["status"] == 1 for item in entries)
            near(sum(item[key] for item in entries; init=0.0), row[index]/base)
        end
    end
    for (i, row) in enumerate(original["gen"])
        g = data["gen"][string(i)]; cost = original["gencost"][i]
        @assert g["gen_bus"] == row[1] && g["gen_status"] == row[8] == 1
        for (key, index) in (("pmax", 9), ("pmin", 10), ("qmax", 4), ("qmin", 5))
            near(g[key], row[index]/base)
        end
        near(g["vg"], row[6])
        @assert g["model"] == cost[1] == 2 && g["ncost"] == cost[4] == 3
        for (actual, expected) in zip(g["cost"], [cost[5]*base^2, cost[6]*base, cost[7]])
            near(actual, expected)
        end
    end
    for (i, row) in enumerate(original["branch"])
        b = data["branch"][string(i)]
        @assert b["f_bus"] == row[1] && b["t_bus"] == row[2]
        @assert b["br_status"] == row[11] == 1
        for (key, expected) in (("br_r", row[3]), ("br_x", row[4]),
            ("g_fr", 0.), ("g_to", 0.), ("b_fr", row[5]/2), ("b_to", row[5]/2),
            ("tap", row[9] == 0 ? 1. : row[9]), ("shift", deg2rad(row[10])),
            ("angmin", deg2rad(row[12])), ("angmax", deg2rad(row[13])))
            near(b[key], expected)
        end
        if row[6] == 0
            @assert !haskey(b, "rate_a")
        else
            near(b["rate_a"], row[6]/base)
        end
    end
    @assert all(isempty(data[key]) for key in ("storage", "dcline", "switch"))
    return true
end

function build_matched(pm; enforce_vset=false)
    PM.variable_bus_voltage_magnitude_sqr(pm)
    PM.variable_buspair_voltage_product(pm; bounded=false)
    PM.variable_gen_power(pm)
    PM.variable_branch_power(pm)
    PM.variable_dcline_power(pm)
    PM.objective_min_fuel_and_flow_cost(pm)
    PM.constraint_model_voltage(pm)
    for i in PM.ids(pm, :bus)
        PM.constraint_power_balance(pm, i)
    end
    for i in PM.ids(pm, :branch)
        PM.constraint_ohms_yt_from(pm, i)
        PM.constraint_ohms_yt_to(pm, i)
        # Deliberately no constraint_voltage_angle_difference: it adds cuts too.
        PM.constraint_thermal_limit_from(pm, i)
        PM.constraint_thermal_limit_to(pm, i)
    end
    if enforce_vset
        selected = Set{Int}()
        for i in sort(collect(PM.ids(pm, :gen)))
            g = PM.ref(pm, :gen, i); b = g["gen_bus"]
            if PM.ref(pm, :bus, b)["bus_type"] in (2, 3) && !(b in selected)
                PM.constraint_voltage_magnitude_setpoint(pm, PM.nw_id_default, b, g["vg"])
                push!(selected, b)
            end
        end
    end
    # Structural guard: no rectangular edge bounds accidentally inherited.
    for key in (:wr, :wi), variable in values(PM.var(pm, key))
        @assert !JuMP.has_lower_bound(variable) && !JuMP.has_upper_bound(variable)
    end
end

function generate()
    request = JSON.parsefile(joinpath(RAW, "request.json"))
    manifest = joinpath(ROOT, "reference_env", "Manifest.toml")
    packages = Dict(info.name => Dict("version" => string(info.version),
        "git_tree_sha1" => string(info.tree_hash)) for info in values(Pkg.dependencies())
        if info.version !== nothing && info.tree_hash !== nothing)
    pm_source = dirname(pathof(PowerModels))
    source_hashes = Dict(file => bytes2hex(sha256(read(joinpath(pm_source, file))))
        for file in ("form/wr.jl", "form/shared.jl", "core/variable.jl",
                     "core/objective.jl", "io/matpower.jl", "core/data.jl"))
    for name in ("two_bus", "case9", "case14")
        req = request["cases"][name]
        # No correct_network_data!: it can clip angles or rewrite ratings/data.
        data = PM.parse_file(joinpath(RAW, name*".m"); validate=false)
        PM.make_per_unit!(data)
        check_parser(data, req["case"])
        open(joinpath(RAW, name*"_parsed.json"), "w") do io
            JSON.print(io, data, 2)
        end
        start = time()
        pm = PM.instantiate_model(data, PM.SOCWRConicPowerModel,
            pm -> build_matched(pm; enforce_vset=req["policy"]["enforce_vset"]))
        build_s = time()-start
        optimizer = JuMP.optimizer_with_attributes(Clarabel.Optimizer,
            (key => value for (key, value) in request["settings"])..., "verbose" => false)
        start = time()
        result = PM.optimize_model!(pm; optimizer=optimizer)
        solve_wall_s = time()-start
        @assert JuMP.termination_status(pm.model) == JuMP.MOI.OPTIMAL result
        @assert JuMP.primal_status(pm.model) == JuMP.MOI.FEASIBLE_POINT
        base = req["case"]["baseMVA"]
        bus_ids = [Int(row[1]) for row in req["case"]["bus"]]
        ordered = sort(collect(PM.ids(pm, :buspairs)); by=p -> minmax(p...))
        pairs = [collect(minmax(p...)) for p in ordered]
        wr = [JuMP.value(PM.var(pm, :wr, p)) for p in ordered]
        wi = [(p[1] < p[2] ? 1 : -1)*JuMP.value(PM.var(pm, :wi, p)) for p in ordered]
        primal = Dict("bus_ids" => bus_ids, "voltage_product_pairs" => pairs,
            "w" => [JuMP.value(PM.var(pm, :w, i)) for i in bus_ids],
            "W_re" => wr, "W_im" => wi,
            "Pg" => [base*JuMP.value(PM.var(pm, :pg, i)) for i in 1:length(data["gen"])],
            "Qg" => [base*JuMP.value(PM.var(pm, :qg, i)) for i in 1:length(data["gen"])])
        for (field, key, reverse) in (("branch_p_from", :p, false), ("branch_q_from", :q, false),
                                    ("branch_p_to", :p, true), ("branch_q_to", :q, true))
            primal[field] = [base*JuMP.value(PM.var(pm, key,
                reverse ? (i, Int(row[2]), Int(row[1])) : (i, Int(row[1]), Int(row[2]))))
                for (i, row) in enumerate(req["case"]["branch"])]
        end
        record = Dict("schema_version" => 1, "case_name" => name, "case" => req["case"],
            "input_sha256" => req["input_sha256"], "policy" => req["policy"],
            "primal" => primal, "objective" => result["objective"],
            "objective_components" => Dict("generator_cost" => result["objective"]),
            "termination" => string(JuMP.termination_status(pm.model)),
            "primal_status" => string(JuMP.primal_status(pm.model)),
            "timing" => Dict("build_s" => build_s, "solve_wall_s" => solve_wall_s,
                             "solver_s" => JuMP.solve_time(pm.model)),
            "reference" => Dict("implementation" => "PowerModels.SOCWRConicPowerModel",
                "julia" => string(VERSION), "machine" => Sys.MACHINE,
                "packages" => packages, "settings" => request["settings"],
                "manifest_sha256" => bytes2hex(sha256(read(manifest))),
                "generator_sha256" => bytes2hex(sha256(read(@__FILE__))),
                "powermodels_source_sha256" => source_hashes,
                "parser_normalization" => ["validate=false (no corrections)",
                    "zero taps to unity", "charging split equally", "zero ratings omitted",
                    "loads/shunts separated", "make_per_unit! powers/costs/angles"],
                "parser_verified" => true))
        open(joinpath(RAW, name*"_powermodels.json"), "w") do io
            JSON.print(io, record, 2)
        end
        println(name, ": ", record["termination"], " objective=", record["objective"])
    end
end

generate()
