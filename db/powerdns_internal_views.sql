begin;

create or replace view dns_internal.domains as
select
    server_id as id,
    hostname::text as name,
    null::text as master,
    null::int as last_check,
    'NATIVE' as type,
    null::int as notified_serial,
    null::text as account
from public.server
where servertype_id = 'provider_domain';

create or replace view dns_internal.records as
select
    0 as id,
    d.id as domain_id,
    r.name,
    r.type,
    r.content,
    86400 as ttl,
    0 as prio,
    0 as change_date,
    false as disabled,
    null::text as ordername,
    true as auth
from (
    select
        server.hostname::text as name,
        v.type,
        v.content
    from public.server
    cross join (values
        ('SOA', 'dnspub-af.innogames.de. hostmaster.innogames.de.'),
        ('NS', 'dnspub-af.innogames.de'),
        ('NS', 'dnspub-aw.innogames.de'),
        ('NS', 'dnspub-vn.innogames.de')
    ) as v(type, content)
    where server.servertype_id = 'provider_domain'
union all
    select
        server.hostname::text as name,
        NULL::text as type,
        NULL::text as content
    from public.server
    where server.servertype_id = 'project_domain'
union all
    select
        server.hostname::text as name,
        case family(server.intern_ip) when 4 then 'A' else 'AAAA' end as type,
        host(server.intern_ip) as content
    from public.server
    where server.intern_ip is not null
union all
    select
        server.hostname::text as name,
        case family(attribute.value) when 4 then 'A' else 'AAAA' end as type,
        host(attribute.value) as content
    from public.server
    join public.server_inet_attribute as attribute using (server_id)
    where server.intern_ip is not null
union all
    select
        domain.hostname::text as name,
        case family(server.intern_ip) when 4 then 'A' else 'AAAA' end as type,
        host(server.intern_ip) as content
    from public.server
    join public.server_relation_attribute as domain_attribute using (server_id)
    join public.server as domain on domain_attribute.value = domain.server_id
    where server.intern_ip is not null and
        domain_attribute.attribute_id = 'domain'
union all
    select
        domain.hostname::text as name,
        case family(attribute.value) when 4 then 'A' else 'AAAA' end as type,
        host(attribute.value) as content
    from public.server
    join public.server_inet_attribute as attribute using (server_id)
    join public.server_relation_attribute as domain_attribute using (server_id)
    join public.server as domain on domain_attribute.value = domain.server_id
    where server.intern_ip is not null and
        domain_attribute.attribute_id = 'domain'
union all
    select
        server.hostname::text as name,
        'MX' as type,
        mx.hostname::text as content
    from public.server
    join public.server_relation_attribute as mx_attribute using (server_id)
    join public.server as mx on mx_attribute.value = mx.server_id
    where mx_attribute.attribute_id = 'mx'
union all
    select
        server.hostname::text as name,
        'SSHFP' as type,
        attribute.value::text as content
    from public.server
    join public.server_string_attribute as attribute using (server_id)
    where server.intern_ip is not null and
        attribute.attribute_id = 'sshfp'
union all
    select
        public.ptr(server.intern_ip) as name,
        'PTR' as type,
        server.hostname::text as content
    from public.server
    where server.intern_ip is not null and
        hostmask(server.intern_ip) in ('0.0.0.0', '::')
union all
    select
        public.ptr(attribute.value) as name,
        'PTR' as type,
        server.hostname::text as content
    from public.server
    join public.server_inet_attribute as attribute using (server_id)
    where server.intern_ip is not null and
        hostmask(attribute.value) in ('0.0.0.0', '::')
) as r
join dns_internal.domains as d
    on r.name like ('%' || d.name);

grant usage on schema dns_internal to dns_internal;
grant select on dns_internal.domains to dns_internal;
grant select on dns_internal.records to dns_internal;
grant select on public.server to dns_internal;
grant select on public.server_string_attribute to dns_internal;

commit;