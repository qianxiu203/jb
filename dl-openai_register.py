def main() -> None:
    parser = argparse.ArgumentParser(description="OpenAI 自动注册脚本（US代理版）")
    parser.add_argument("--proxy", default=None, help="指定单个代理（优先于自动获取）")
    parser.add_argument("--once", action="store_true", help="只运行一次")
    parser.add_argument("--sleep-min", type=int, default=60, help="成功注册后最短等待秒数")
    parser.add_argument("--sleep-max", type=int, default=120, help="成功注册后最长等待秒数")
    parser.add_argument("--skip-check", action="store_true", help="跳过代理可用性检测（ faster but riskier）")
    args = parser.parse_args()

    print("[Info] Yasal's Seamless OpenAI Auto-Registrar + US Proxy Pool")
    
    # 代理管理
    proxy_pool = []
    proxy_iter = None
    
    if args.proxy:
        # 使用指定代理
        proxy_pool = [args.proxy]
        print(f"[*] 使用指定代理: {args.proxy}")
    else:
        # 从网络获取代理池
        print("[*] 正在获取 US 免费代理列表...")
        proxy_pool = fetch_us_proxies()
        if not proxy_pool:
            print("[Error] 无法获取代理列表，退出")
            return
        # 随机打乱，避免总是用前几个
        random.shuffle(proxy_pool)
        proxy_iter = itertools.cycle(proxy_pool)  # 循环迭代器
    
    count = 0
    failed_proxies = set()  # 记录已失效的代理
    
    while True:
        count += 1
        current_proxy = None
        
        # 获取一个可用代理
        if args.proxy:
            current_proxy = args.proxy
        else:
            # 从代理池中找到下一个未失效的代理
            for _ in range(len(proxy_pool)):
                candidate = next(proxy_iter)
                if candidate in failed_proxies:
                    continue
                
                # 验证代理（除非跳过）
                if not args.skip_check:
                    print(f"[*] 测试代理 {candidate}...", end="", flush=True)
                    if check_proxy_usable(candidate):
                        current_proxy = candidate
                        break
                    else:
                        print(" 不可用，跳过")
                        failed_proxies.add(candidate)
                else:
                    current_proxy = candidate
                    break
            
            if not current_proxy:
                print("[Error] 代理池耗尽，重新获取列表...")
                proxy_pool = fetch_us_proxies()
                random.shuffle(proxy_pool)
                proxy_iter = itertools.cycle(proxy_pool)
                failed_proxies.clear()
                continue
        
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] >>> 第 {count} 次尝试 | 代理: {current_proxy} <<<")
        
        try:
            token_json = run(current_proxy)
            
            if token_json:
                try:
                    t_data = json.loads(token_json)
                    fname_email = t_data.get("email", "unknown").replace("@", "_")
                except Exception:
                    fname_email = "unknown"
                
                file_name = f"token_{fname_email}_{int(time.time())}.json"
                with open(file_name, "w", encoding="utf-8") as f:
                    f.write(token_json)
                print(f"[√] 成功! Token 已保存至: {file_name}")
                
                # 成功注册后，标记代理为"良好"，可以复用或记录
                if args.once:
                    break
                    
                wait_time = random.randint(args.sleep_min, args.sleep_max)
                print(f"[*] 注册成功，休息 {wait_time} 秒后继续...")
                time.sleep(wait_time)
                
            else:
                print("[-] 注册失败，更换代理...")
                failed_proxies.add(current_proxy)  # 失败可能是因为代理被封
                
        except Exception as e:
            print(f"[Error] 异常: {e}")
            failed_proxies.add(current_proxy)  # 异常代理标记为失效
            
        if args.once:
            break
            
        time.sleep(random.randint(5, 10))  # 失败间隔短一些，快速换代理
