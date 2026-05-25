import base64
import os

from openai import OpenAI


#  编码函数： 将本地文件转换为 Base64 编码的字符串
def encode_image(image_path: str) -> str:
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")


def main() -> None:
    # 将 xxxx/eagle.png 替换为你本地图像的绝对路径
    base64_image = encode_image("./image.png")
    client = OpenAI(
        # 若没有配置环境变量，请设置 DASHSCOPE_API_KEY。
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        # 各地域配置不同，请根据实际地域修改
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    completion = client.chat.completions.create(
        model="qwen3.6-flash",  # 此处以 qwen3.6-flash 为例，可按需更换模型名称
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        # 图像格式需与 Content Type 保持一致。
                        "image_url": {"url": f"data:image/png;base64,{base64_image}"},
                    },
                    {"type": "text", "text": "图中描绘的是什么景象?"},
                ],
            }
        ],
    )
    print(completion.choices[0].message.content)


if __name__ == "__main__":
    main()
