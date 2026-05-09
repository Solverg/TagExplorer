import random
from pathlib import Path


def create_stress_folder() -> None:
    target_dir = Path("STRESS_TEST_50K")
    target_dir.mkdir(exist_ok=True)

    tags = ["projectA", "urgent", "review", "archive", "media", "doc"]
    exts = [".txt", ".jpg", ".png", ".mp4", ".pdf", ".docx"]

    print("Генерация 50,000 файлов. Пожалуйста, подождите...")
    for i in range(50000):
        num_tags = random.randint(0, 2)
        file_tags = "".join(f"[{random.choice(tags)}]" for _ in range(num_tags))
        ext = random.choice(exts)
        filename = f"{file_tags} test_file_{i}{ext}"

        filepath = target_dir / filename
        filepath.touch()

        if i % 5000 == 0 and i > 0:
            print(f"Создано {i} файлов...")

    print(f"\nУспешно! Откройте папку {target_dir.resolve()} в TagExplorer.")


if __name__ == "__main__":
    create_stress_folder()
